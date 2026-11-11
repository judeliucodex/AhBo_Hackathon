// Canned tool results for "Force tool result" (§12.1 overrides, §13.1 backups). Shapes follow CONTRACTS §6
// and values follow seed/demo.yaml (fictional demo data). The operator can edit the JSON before sending.

export const TOOLS = [
  "get_time", "get_weather", "get_med_status", "confirm_taken", "snooze_dose", "list_events", "add_event",
  "get_day_plan", "get_route", "find_local_activities", "check_activity_logistics", "recall", "remember",
  "recall_life_story", "list_voice_messages", "play_voice_message", "send_message_to_family", "deep_task",
  "request_action", "confirm_action", "raise_concern", "get_incident_status", "show_card",
];

const med = (id, yue, en, dose, pill) => ({ id, yue, en, dose, pill });

export const CANNED = {
  get_time: { "10:00": { ok: true, date: "2026-11-20", weekday_words: "星期五", time: "10:00", time_words: "十點正", period: "朝早" } },
  get_weather: {
    "fine (fake)": { ok: true, now_words: "而家天晴，二十三度", temp_c: 23, forecast_words: "今日天晴，下晝有微風", weather_short: "天晴", source: "fake" },
  },
  get_med_status: {
    "S2: next 12:00, four meds": {
      ok: true,
      next: { slot_id: "s_1200", time: "12:00", time_words: "十二點正", rel_words: "兩個鐘之後", count: 4,
        meds: [med("bp", "血壓藥", "Amlodipine", "5 mg", ["--pill-b", "--pill-a"]),
               med("dm", "糖尿藥", "Metformin", "500 mg", ["--pill-a", "--pill-a"]),
               med("chol", "膽固醇藥", "Atorvastatin", "20 mg", ["--pill-a", "--pill-c"]),
               med("asp", "薄血丸", "Aspirin", "80 mg", ["--pill-c", "--pill-a"])] },
      today: [{ slot_id: "s_0800", time: "08:00", time_words: "八點", state: "taken_self_reported", meds: ["糖尿藥"] },
              { slot_id: "s_1200", time: "12:00", time_words: "十二點正", state: "pending", meds: ["血壓藥", "糖尿藥", "膽固醇藥", "薄血丸"] },
              { slot_id: "s_2000", time: "20:00", time_words: "八點", state: "pending", meds: ["糖尿藥"] }],
      taken_today: ["s_0800"],
    },
    "nothing pending": { ok: true, next: null, today: [], taken_today: [] },
  },
  confirm_taken: {
    "taken": { ok: true, slot_id: "s_1200", status: "taken_self_reported" },
    "no dose pending": { ok: false, reason: "no_dose_pending" },
  },
  snooze_dose: { "10 min": { ok: true, slot_id: "s_1200", until: "12:10" }, "max snoozes": { ok: false, reason: "max_snoozes" } },
  list_events: {
    "S3: today": { ok: true, count: 3, count_words: "三樣", events: [
      { id: "ev_1", time: "09:30", time_words: "九點半", title: "出門", place: "樓下巴士站", kind: "plan", status: "confirmed" },
      { id: "ev_2", time: "10:30", time_words: "十點半", title: "覆診", place: "聯合醫院", kind: "medical", status: "confirmed" },
      { id: "ev_3", time: "12:30", time_words: "十二點半", title: "同阿珍飲茶", place: "觀塘", kind: "meal", status: "confirmed" }] },
    "empty": { ok: true, events: [], count: 0, count_words: "冇" },
  },
  add_event: { "added": { ok: true, id: "ev_new" } },
  get_day_plan: { "seed plan": { ok: true, date: "2026-11-20", summary: "朝早九點半出門去聯合醫院覆診，十二點半同阿珍飲茶，下晝三點可以去長者中心粵曲班。", plan: null } },
  get_route: { "hospital": { ok: true, destination: "聯合醫院", place_short: "聯合醫院", route_text: "樓下巴士站搭巴士去聯合醫院，大約二十五分鐘", minutes: 25 } },
  find_local_activities: {
    "opera 15:00": { ok: true, top_title: "粵曲班", activities: [
      { id: "act_opera_1500", title: "粵曲班", venue: "秀茂坪長者中心（示範）", venue_short: "長者中心", time: "15:00",
        time_words: "下晝三點", walk_minutes: 10, signup: { method: "web_form", url: "/mock-centre/" } }] },
  },
  check_activity_logistics: { "free": { ok: true, activity_id: "act_opera_1500", free: true, conflicts: [], walk_minutes: 10, free_words: "你下晝得閒", travel_words: "行路十分鐘" } },
  recall: { "yesterday": { ok: true, facts: [], episodes: [{ session_id: "sess_seed_1", date: "昨日", summary: "佢話琴日膝頭有啲痛，所以冇落街。" }] } },
  remember: { "ok": { ok: true, id: "fact_new" } },
  recall_life_story: { "teaching": { ok: true, story: { id: "ls_1", title: "教書嘅日子", text: "王婆婆喺觀塘一間小學教咗三十年中文，好多學生大個咗仲返嚟探佢。", era: "1970s-2000s" } } },
  list_voice_messages: { "S1: 阿珍 07:42": { ok: true, has_new: true, from_name: "阿珍", messages: [{ id: "vm_demo", from_name: "阿珍", received_words: "今朝七點四十二分", duration_s: 4.1, played: false }] } },
  play_voice_message: { "started": { ok: true, id: "vm_demo", started: true } },
  send_message_to_family: { "sent": { ok: true, contact: "阿珍" } },
  deep_task: { "started": { ok: true, status: "started", task_id: "deep_forced" }, "busy": { ok: false, status: "busy" } },
  request_action: { "signup": { ok: true, action_id: "act_forced", question: "想唔想我幫你報名？" } },
  confirm_action: { "running": { ok: true, status: "running" }, "low confidence": { ok: false, reason: "low_confidence" } },
  raise_concern: { "info": { ok: true, level: "info" } },
  get_incident_status: { "help coming": { ok: true, state: "ESCALATED", words: "已經通知咗阿珍，救護車就嚟到" } },
  show_card: { "ok": { ok: true } },
};

export function cannedFor(tool) {
  return CANNED[tool] || { "ok": { ok: true } };
}
