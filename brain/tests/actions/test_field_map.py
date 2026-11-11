"""Field mapping + data minimisation (`actions/field_map.py`, §4.3.5): only shareable fields are ever typed;
forbidden fields and unknown required fields are hard stops; the real-mode LLM only proposes names and is
re-validated."""
from __future__ import annotations

from typing import Any

from ahbo.actions import field_map as fm
from ahbo.actions.field_map import ActionChoice, FormField, allowed_sources, choose_option, map_fields

SHAREABLE = {"name_zh": "王婆婆", "name_en": "Wong Po-po", "age_group": "75-79",
             "contact_phone": "91234567", "district": "觀塘", "language": "廣東話"}
CHOICE = ActionChoice(activity_id="act_opera_1500", activity_title="粵曲班", time="15:00",
                      session_value="15:00-16:30")
FORBIDDEN = ["香港身份證", "身份證號碼", "HKID", "ID number", "信用卡", "card number", "密碼", "password", "銀行"]


def mock_fields() -> list[FormField]:
    return [
        FormField(key="#name_zh", label="姓名（中文）", name="name_zh", id="name_zh", required=True),
        FormField(key="#name_en", label="英文姓名", name="name_en", id="name_en"),
        FormField(key="#age_group", label="年齡組別", id="age_group", tag="select", required=True,
                  options=[{"value": v, "text": f"{v}歲"} for v in ("60-64", "65-69", "70-74", "75-79")]),
        FormField(key="#phone", label="聯絡電話", name="phone", id="phone", type="tel", required=True),
        FormField(key="#activity", label="活動", id="activity", tag="select", required=True,
                  options=[{"value": "act_opera_1500", "text": "粵曲班"},
                           {"value": "act_craft_1430", "text": "手工班"}]),
        FormField(key="#session", label="時段", id="session", tag="select", required=True,
                  options=[{"value": "", "text": "請先選擇活動"},
                           {"value": "15:00-16:30", "text": "15:00–16:30"}]),
    ]


async def plan(fields: list[FormField], shareable: dict[str, Any] = SHAREABLE, llm: Any = None) -> fm.MapResult:
    return await map_fields(fields, shareable, CHOICE, FORBIDDEN, llm)


def fills_by_source(r: fm.MapResult) -> dict[str, fm.Fill]:
    return {f.source: f for f in r.fills}


async def test_deterministic_map_fills_only_shareable():
    r = await plan(mock_fields())
    assert r.ok and r.mapper == "deterministic"
    f = fills_by_source(r)
    assert f["name_zh"].value == "王婆婆" and f["name_zh"].kind == "fill" and f["name_zh"].key == "#name_zh"
    assert f["name_en"].value == "Wong Po-po"
    assert f["age_group"].value == "75-79" and f["age_group"].kind == "select"
    assert f["contact_phone"].value == "91234567"
    assert f["activity"].value == "act_opera_1500"
    assert f["session"].value == "15:00-16:30"          # en-dash text matches via normalisation
    assert f["session"].label != f["activity"].label         # the two selects map to different rules


async def test_allowed_sources_follow_shareable():
    assert "contact_phone" in allowed_sources(SHAREABLE)
    assert "dob" not in allowed_sources(SHAREABLE)
    assert "dob" in allowed_sources({**SHAREABLE, "dob": "1948-01-01"})
    assert set(fm.ACTION_FIELDS) <= allowed_sources({})


async def test_forbidden_fields_are_hard_stops():
    fields = mock_fields() + [
        FormField(key="#hkid", label="身份證號碼", id="hkid", required=True),
        FormField(key="#pwd", label="請設定密碼", id="pwd", type="password"),
        FormField(key="#card", label="信用卡號碼", id="card", autocomplete="cc-number"),
        FormField(key="#bank", label="銀行戶口", id="bank", required=True),
        FormField(key="#dob", label="出生日期", id="dob", required=True),
    ]
    r = await plan(fields)
    assert not r.ok and r.reason == "forbidden_field"
    labels = " ".join(r.forbidden)
    for word in ("身份證號碼", "請設定密碼", "信用卡號碼", "銀行戶口", "出生日期"):
        assert word in labels
    assert r.fills                                       # the allowed fields are still mapped


async def test_unknown_required_field_blocks_but_optional_is_skipped():
    fields = mock_fields() + [FormField(key="#carer", label="緊急聯絡人", id="carer", required=True),
                              FormField(key="#note", label="備註", id="note", tag="textarea")]
    r = await plan(fields)
    assert not r.ok and r.reason == "unknown_required_field" and r.unknown_required == ["緊急聯絡人"]
    assert r.skipped_optional == ["備註"]


async def test_value_shape_rules():
    # age as a number input can't be verified against a select of groups → unknown required, never typed
    fields = [FormField(key="#age_n", label="年齡", id="age_n", type="number", required=True)]
    r = await plan(fields)
    assert not r.ok and r.reason == "unknown_required_field"
    # a select without a matching option → unknown required
    fields = [FormField(key="#lang", label="語言", id="lang", tag="select", required=True,
                        options=[{"value": "en", "text": "English"}])]
    r = await plan(fields)
    assert not r.ok and r.reason == "unknown_required_field"


def test_choose_option_exact_then_containment():
    f = FormField(key="#s", tag="select",
                  options=[{"value": "a", "text": "請選擇"}, {"value": "v15", "text": "15:00–16:30"},
                           {"value": "v09", "text": "朝早9:00"}])
    assert choose_option(f, ["v15"]) == "v15"                       # exact option value
    assert choose_option(f, ["15:00-16:30"]) == "v15"               # containment after – → - normalisation
    assert choose_option(f, ["12:00"]) is None


async def test_cascading_select_with_no_options_is_deferred_not_unknown():
    """The 時段 dropdown is empty until 活動 is chosen: defer it, never hard-stop at map time."""
    fields = [f for f in mock_fields() if f.key != "#session"] + \
        [FormField(key="#session", label="時段", id="session", tag="select", required=True, options=[])]
    r = await plan(fields)
    assert r.ok
    session = fills_by_source(r)["session"]
    assert session.kind == "select_deferred" and session.value == ""
    # and with no approved activity/time at all there is nothing to defer → unknown required
    r2 = await map_fields(fields, SHAREABLE, ActionChoice(), FORBIDDEN, None)
    assert not r2.ok and r2.reason == "unknown_required_field"


class FakeLLM:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages, tools=None, *, purpose="signup_fieldmap", thinking=None,
                   correlation_id=None):
        self.calls.append({"purpose": purpose, "correlation_id": correlation_id})
        return self


async def test_llm_proposes_names_only_and_is_revalidated():
    llm = FakeLLM('{"map": {"#name_zh": "name_zh", "#phone": "contact_phone", "#carer": "name_zh"}}')
    fields = mock_fields() + [FormField(key="#carer", label="緊急聯絡人", id="carer", required=True)]
    r = await plan(fields, llm=llm)
    # the LLM cannot make a REQUIRED field fillable (data minimisation): 緊急聯絡人 stays unknown → hard stop
    assert not r.ok and r.reason == "unknown_required_field"
    assert llm.calls[0]["purpose"] == "signup_fieldmap"
    # the required fields still map deterministically
    assert fills_by_source(r)["name_zh"].value == "王婆婆"
    assert r.mapper == "llm+deterministic"


async def test_llm_can_map_optional_fields_the_rules_missed():
    llm = FakeLLM('{"map": {"#how_hear": "district"}}')
    fields = mock_fields() + [FormField(key="#how_hear", label="你由邊度知識呢個班？", id="how_hear")]
    r = await plan(fields, llm=llm)
    assert r.ok and fills_by_source(r)["district"].value == "觀塘"   # optional → LLM proposal applies


async def test_llm_garbage_falls_back_to_deterministic():
    for bad in ("not json", '{"map": "#name_zh"}', ''):
        r = await plan(mock_fields(), llm=FakeLLM(bad))
        assert r.ok and r.mapper == "deterministic"
    # valid JSON whose keys match nothing: every real field keeps its deterministic value
    r = await plan(mock_fields(), llm=FakeLLM('{"map": {"#nope": "name_zh"}}'))
    assert r.ok and r.mapper == "llm+deterministic"
    llm = FakeLLM('{"map": {"#name_zh": "secret_nickname", "#activity": "act_opera_1500"}}')
    r = await plan(mock_fields(), llm=llm)
    assert r.ok and fills_by_source(r)["name_zh"].value == "王婆婆"    # invalid proposal → deterministic value


async def test_llm_cannot_unforbid():
    llm = FakeLLM('{"map": {"#hkid": null}}')
    fields = mock_fields() + [FormField(key="#hkid", label="身份證號碼", id="hkid", required=True)]
    r = await plan(fields, llm=llm)
    assert not r.ok and r.reason == "forbidden_field"


async def test_llm_failure_falls_back(monkeypatch):
    class Boom:
        async def chat(self, *a, **k):
            raise RuntimeError("provider down")

    r = await plan(mock_fields(), llm=Boom())
    assert r.ok and r.mapper == "deterministic"
