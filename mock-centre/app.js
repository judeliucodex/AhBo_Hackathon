// 秀茂坪長者中心（示範）: activity registration (mock site, §8.3). No external dependencies.
// Activities come from GET api/activities (served from seed/activities.yaml, the one source of truth).
// ?variant=hkid|captcha|login|payment adds a block that the Action Runner must treat as a hard stop.
(function () {
  "use strict";

  var form = document.getElementById("signup-form");
  var activitySel = document.getElementById("activity");
  var sessionSel = document.getElementById("session");
  var errorBox = document.getElementById("form-error");
  var submitBtn = document.getElementById("submit-btn");
  var params = new URLSearchParams(location.search);
  var variant = (params.get("variant") || "").toLowerCase();
  var activities = [];

  function show(id, required) {
    var el = document.getElementById(id);
    if (!el) return;
    el.hidden = false;
    if (required) {
      el.querySelectorAll("input").forEach(function (i) {
        if (i.type !== "checkbox") i.required = true;
      });
      var input = el.tagName === "INPUT" ? el : null;
      if (input) input.required = true;
    }
  }

  if (variant === "hkid") show("hkid-field", true);
  if (variant === "captcha") show("captcha-panel", true);
  if (variant === "payment") show("payment-panel", true);
  if (variant === "login") {
    show("login-panel", false);
    document.getElementById("member_id").required = true;
    document.getElementById("member_password").required = true;
  }

  function option(value, text) {
    var o = document.createElement("option");
    o.value = value;
    o.textContent = text;
    return o;
  }

  function fillSessions() {
    var act = activities.find(function (a) { return a.id === activitySel.value; });
    sessionSel.innerHTML = "";
    if (!act) {
      sessionSel.appendChild(option("", "請先選擇活動"));
      return;
    }
    sessionSel.appendChild(option("", "請選擇"));
    act.sessions.forEach(function (s) { sessionSel.appendChild(option(s.value, s.label)); });
  }

  activitySel.addEventListener("change", fillSessions);

  fetch("api/activities")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      activities = data.activities || [];
      activities.forEach(function (a) { activitySel.appendChild(option(a.id, a.label)); });
      document.body.setAttribute("data-ready", "1");
    })
    .catch(function () {
      errorBox.textContent = "暫時未能載入活動資料，請稍後再試。";
      errorBox.hidden = false;
    });

  function fail(msg, el) {
    errorBox.textContent = msg;
    errorBox.hidden = false;
    if (el) el.focus();
  }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    errorBox.hidden = true;
    var missing = Array.prototype.find.call(form.querySelectorAll("[required]"), function (el) {
      return !el.closest("[hidden]") && !String(el.value || "").trim();
    });
    if (missing) {
      var label = form.querySelector('label[for="' + missing.id + '"]');
      fail("請填寫：" + (label ? label.textContent.replace("*", "").trim() : missing.name), missing);
      return;
    }
    var body = { variant: variant || null };
    ["name_zh", "name_en", "age_group", "phone", "activity", "session"].forEach(function (k) {
      body[k] = String(form.elements[k].value || "").trim();
    });
    submitBtn.disabled = true;
    fetch("api/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    })
      .then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); })
      .then(function (res) {
        submitBtn.disabled = false;
        if (!res.body.ok) {
          fail(res.body.message || "報名未能完成，請檢查資料。");
          return;
        }
        document.getElementById("booking-ref").textContent = res.body.reference;
        var dl = document.getElementById("confirm-details");
        dl.innerHTML = "";
        [["姓名", body.name_zh], ["活動", res.body.activity_label], ["時段", res.body.session_label]].forEach(function (p) {
          var dt = document.createElement("dt"); dt.textContent = p[0];
          var dd = document.createElement("dd"); dd.textContent = p[1] || "";
          dl.appendChild(dt); dl.appendChild(dd);
        });
        form.hidden = true;
        document.getElementById("confirmation").hidden = false;
        document.title = "報名成功｜秀茂坪長者中心（示範）";
      })
      .catch(function () {
        submitBtn.disabled = false;
        fail("網絡有問題，請稍後再試。");
      });
  });
})();
