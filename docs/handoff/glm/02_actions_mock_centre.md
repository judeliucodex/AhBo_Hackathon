# GLM task 02: Actions runner (Playwright sign-up) + mock registration site

Read `00_COMMON.md` first. Report goes to `docs/handoff/glm/02_REPORT.md`.

**PRD sections:** §4.3.5 (external action machine, data minimisation, domains), §8.3 (Action Runner, Playwright,
hard stops, mock site), §11 (cap: 10 Playwright actions/day), §20 (`signup_fieldmap` purpose), §24.14, §26.4 (mock-centre
endpoints), §26.5 (`action_domains.yaml`).

**You own:** `brain/ahbo/actions/**`, `mock-centre/**` (entry `mock-centre/index.html`), `brain/tests/actions/**`.

**Already on disk from a stopped agent (review, keep what is right, finish):** `actions/{domains,field_map,activities,mock_centre}.py`
and files in `mock-centre/`. No tests yet; no `runner.py`, `playwright_form.py`, `telegram_msg.py`, `wiring.py` yet.

**How it plugs in:** the platform calls `ahbo.actions.wiring.install(svc)` and includes `ahbo.actions.wiring.router` if they
exist (see `brain/ahbo/api/services.py`). The platform already serves `mock-centre/` statically at `/mock-centre/` with no auth;
you provide only the API route. The Kernel (being built by Claude now) emits the command `action.run {action_id}` after Po-po
says yes; you dispatch it and report back with `action.status` events (CONTRACTS §4.1). The `actions` table is in
`brain/ahbo/core/db.py`. Resident data the runner may use: `resident.shareable` JSON (seeded by `brain/ahbo/ops/seed.py`).

## Build (each with tests)
1. `mock-centre/index.html` (+ css/js, no external deps): a realistic **Traditional Chinese** registration form for
   「秀茂坪長者中心（示範）」: 姓名（中文）, 英文姓名, 年齡組別, 聯絡電話, 活動 (粵曲班 15:00 etc., from
   `brain/seed/activities.yaml`), 時段. Proper `<label for>` on every field. Submit → `POST /mock-centre/api/register` →
   show a booking reference (e.g. `DEMO-0423`). Footer clearly 「示範用 Demo」. Query-string variants to exercise hard stops:
   `?variant=hkid` (adds a required 身份證號碼 field), `?variant=captcha`, `?variant=login`, `?variant=payment`.
2. `actions/mock_centre.py`: FastAPI `router` with `POST /mock-centre/api/register` (validates, stores in a table you create
   with `CREATE TABLE IF NOT EXISTS mock_registrations` at install time — note it in the report), returns the reference. Public.
3. `actions/field_map.py`: map form fields (from labels / the Playwright accessibility snapshot) to **only** these profile
   fields: name_zh, name_en, age_group, contact_phone (阿珍's number), district, language. Deterministic label matching in fake
   mode. Real mode asks Sonnet (purpose `signup_fieldmap`) for a JSON map, but the result is validated against that allowed set —
   the model never types free text. A forbidden field (`forbidden_fields` in `brain/config/action_domains.yaml`: HKID / ID
   number, card, bank, password; date of birth unless shareable) or any **unknown required field** → `needs_human`.
4. `actions/playwright_form.py` + `actions/runner.py`: dispatcher for `action.run`: load the action row → domain allowlist check
   (`allow_submit` after `{PUBLIC_BASE_URL}` substitution; `read_only` domains are never submitted) → connect to the browser
   container with `playwright.async_api` `chromium.connect(settings.browser_ws)` (client and server are both Playwright 1.64.1 —
   VERIFY the signature in the image) → fresh context, downloads off → open → snapshot → map → reject forbidden → fill →
   screenshot `pre_submit.png` → submit → wait for the confirmation → `post_submit.png` → extract the reference. Files go to
   `data/media/actions/<action_id>/`. Hard stops → `needs_human`: CAPTCHA, login form, payment, unknown required field,
   cross-domain redirect, more than 120 s. Emit `action.status` events: `filling`, then `done` (with screenshots + reference),
   `needs_human`, or `failed`. Cap 10/day via `ahbo.ops.caps.check_cap` if it exists (another agent may still be writing it —
   import lazily). Inside Docker the browser must reach the mock site at the internal URL `http://api:8000/mock-centre/`, while
   the allowlist is written with PUBLIC_BASE_URL: implement and document a rewrite of allowlisted public URLs to the internal one.
5. `actions/telegram_msg.py`: the `telegram_message` method: fixed template + event details only (never free LLM text), only
   to allowlisted `contacts`; it asks the Kernel to send by submitting an event (document the event name in your report).
6. `actions/wiring.py`: `install(svc)` (register the `action.run` dispatcher with `svc.kernel.on("action.run", fn)`; the
   dispatcher must return fast and do the work with `svc.kernel.spawn(...)`) and `router`.
7. Tests:
   - unit tests: field mapping, forbidden-field detection, unknown required field, domain allowlist + URL rewrite;
   - **integration test** with real Playwright: create a private network (`docker network create ahbo-t-actions`), run
     `ahbo-browser` with `playwright run-server --port 3000 --host 0.0.0.0` and an `ahbo-api` container serving a tiny test
     FastAPI app (your router + the static `mock-centre/`) on that network; assert `done` with both screenshots and a reference,
     and `needs_human` for `?variant=hkid` and `?variant=captcha`. Remove the containers and the network afterwards.
     Copy one pre/post screenshot pair to `docs/screens/actions/`.

## Done when
- `docker run --rm -v /opt/ahbo:/repo ahbo-api python -m pytest -q tests/actions` passes (the integration test may be a
  separate script if it needs Docker-in-Docker — then document exactly how you ran it and paste its output).
- `https://srv2135273.hstgr.cloud/mock-centre/` is NOT for you to restart, but the page must work when served there
  (static files) — check it renders in your browser test.
- Full suite: nothing new fails.
