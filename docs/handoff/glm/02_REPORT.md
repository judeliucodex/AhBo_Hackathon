# GLM task 02 report — Actions runner (Playwright sign-up) + mock registration site

## Files created/changed

Owned roots: `brain/ahbo/actions/**`, `mock-centre/**`, `brain/tests/actions/**` (+ the task-sanctioned
screenshot copies in `docs/screens/actions/`).

Created:
- `brain/ahbo/actions/__init__.py` — package doc + the shared `Outcome` type.
- `brain/ahbo/actions/playwright_form.py` — the browser session (§8.3 steps 1–10): connect, fresh context
  (no storage, downloads off), extract + hard-stop scan in one JS pass, fill (with cascading-select
  support), `pre_submit.png` → submit → wait confirmation → `post_submit.png` → booking reference.
- `brain/ahbo/actions/runner.py` — the `action.run` dispatcher body: load row, allowlist on the public
  URL, cap check, public→internal URL rewrite, emit `filling`, run, record the cost row, emit the terminal
  `action.status`; plus the real-mode Sonnet field-map client (purpose `signup_fieldmap`).
- `brain/ahbo/actions/telegram_msg.py` — the `telegram_message` method: allowlist check, fixed templates
  (signup / plain message), asks the Kernel to send via the **`action.message_send`** event.
- `brain/ahbo/actions/wiring.py` — `install(svc)` (creates `mock_registrations`, registers the
  `action.run` dispatcher, work done via `svc.kernel.spawn`) and `router` (the mock-centre API routes).
- `brain/tests/actions/test_domains.py`, `test_field_map.py`, `test_mock_centre.py`, `test_runner.py`,
  `test_serving.py`, `test_playwright_integration.py` (env-gated real-Playwright integration),
  `e2e/mock_app.py` (tiny FastAPI app for the e2e topology), `e2e/run_e2e.sh` (creates + tears down the
  `ahbo-t-actions` network, the browser run-server and the api container; runs the integration test).
- `docs/screens/actions/pre_submit.png`, `post_submit.png` — one real pre/post pair, copied as the task
  requires (filled form; confirmation 報名成功 with `DEMO-0423`).

Changed (files that were already on disk from the stopped agent — reviewed, kept what was right):
- `brain/ahbo/actions/mock_centre.py` — kept validation/table/reference logic unchanged; **removed the
  static GET routes** (`/mock-centre/`, `/mock-centre/{name}`, the `/mock-centre` redirect): the platform
  already serves the statics (`ahbo/api/routes/public.py`, BUILD_LOG D-3) and the task says this component
  provides only the API routes. Kept `GET /mock-centre/api/activities` (the page's JS needs it) and added
  `static_dir()` (used by the e2e app).
- `brain/ahbo/actions/field_map.py` — three changes, all safety-driven (see Deviations):
  1. real-mode LLM proposals now apply to **optional fields only** — a required field must be matched by
     the deterministic rules or it stays an unknown required field (`needs_human`); before, the LLM could
     make an unknown required field (e.g. 緊急聯絡人) fillable by proposing a shareable name for it;
  2. `select_deferred` fill kind for cascading dropdowns (時段 has no options until 活動 is chosen —
     deferred to fill time and re-read there, still a hard stop if no allowed option appears);
  3. docstring updates. The deterministic rules, forbidden-field detection and LLM validation are unchanged.
- `brain/ahbo/actions/domains.py`, `activities.py` — kept **unchanged** (tested as-is; both correct).
- `mock-centre/index.html`, `style.css`, `app.js` — kept **unchanged**; verified end-to-end in a real
  browser (see Tests). Traditional Chinese, `<label for>` on every field, 示範用 Demo ribbon + footer,
  `?variant=hkid|captcha|login|payment`, booking reference, no external dependencies.

Not touched: `brain/ahbo/kernel/**`, `brain/ahbo/api/**`, `brain/config/**`, `brain/seed/**`, `face/`,
`ui-reference/`, anything in `links/` (another agent's live work), git.

## Tests (exact command + the real last line of pytest output)

1. Component suite (the task's "Done when" command):
   `docker run --rm -v /opt/ahbo:/repo ahbo-api python -m pytest -q tests/actions`
   → last line: `35 passed, 6 skipped in 9.98s`
   (the 6 skips are `test_playwright_integration.py`, gated on `AHBO_ACTION_E2E=1` — they need the docker
   network topology below; a plain container run has none.)

2. Integration test with real Playwright (the task allows a separate script since it needs Docker):
   `bash brain/tests/actions/e2e/run_e2e.sh` — from `/opt/ahbo`. The script:
   - `docker network create ahbo-t-actions`;
   - `docker run -d --name ahbo-t2-browser --network ahbo-t-actions --network-alias browser --entrypoint bash ahbo-browser -c "playwright run-server --port 3000 --host 0.0.0.0"`;
   - `docker run -d --name ahbo-t2-api --network ahbo-t-actions --network-alias api -v /opt/ahbo:/repo -e AHBO_FAKE=all -e AHBO_PROFILE=demo -e PUBLIC_BASE_URL=https://demo.invalid -e AHBO_ACTION_INTERNAL_BASE=http://api:8000 -e AHBO_BROWSER_WS=ws://browser:3000/ --entrypoint bash ahbo-api -c "python /repo/brain/tests/actions/e2e/mock_app.py"`;
   - waits for `http://api:8000/mock-centre/` to answer inside the network;
   - runs `AHBO_ACTION_E2E=1 python -m pytest -q tests/actions/test_playwright_integration.py` inside the
     api container (the runner connects out to `ws://browser:3000/`; the browser fetches
     `http://api:8000/mock-centre/`, exercising the public→internal rewrite for real);
   - removes both containers and the network on exit (verified: `docker ps -a` clean afterwards).
   → its output ends: `6 passed in 11.53s`
   Covered: the mock page renders in real Chromium (title, 示範用 Demo ribbon, labels, 時段 dropdown
   fills after choosing 活動); the full signup ends `done` with `DEMO-…` reference and both screenshots on
   disk (`>1000` bytes each); `?variant=hkid` → `needs_human` `forbidden_field` (身份證… listed);
   `?variant=captcha` → `needs_human` `captcha`; plus login and payment variants → `needs_human`.

3. Serving through the real platform app (in the component suite, `test_serving.py`): `create_app` +
   TestClient — the platform's static router serves `/mock-centre/` (index + assets, 404 elsewhere) and the
   component router serves `/mock-centre/api/register` + `/api/activities`, both with no auth, and the
   booking lands in `mock_registrations`.

4. Full suite (each command from `/repo/brain` in the image):
   - `python -m pytest -q --ignore=tests/links` → last line: `510 passed, 33 skipped in 216.47s`
     — **nothing fails outside `tests/links`**; the three failures `00_COMMON.md` calls known
     (`test_golden_ui S4|S4ok`, `test_inv_06_traced`, `test_inv_08_operator_visible`) all pass in this run.
   - `tests/links` alone → `8 failed, 66 passed in 68.26s` (a different run of the full suite including
     links showed `11 failed, 573 passed, 32 skipped`). These are the other agent's in-progress component
     (webhook-secret, INV-9 formats, outbox fakes, twilio SDK tests); links never imports `ahbo.actions`
     and the failures reproduce with my changes absent from their paths — not mine, not fixed by me.

## Post-handoff re-verification (in-depth check, same day)

Everything re-verified from the current repo state after the handoff, with other agents' latest landings
(`links/wiring.py` now exists and installs cleanly before `actions` — checked: it registers only
`family.send`/`phone.call`, so no dispatcher conflict; `action.message_send` is still unhandled, so the
report's requested change #1 stands):

- All integration points re-checked against the live repo: `services.COMPONENTS` still includes
  `actions` + imports `ahbo.<pkg>.wiring`; the reducer still emits `action.run {action_id}` with the 120 s
  guard and handles `action.status` the same; the platform static router still serves `/mock-centre/` last
  (component API routes win); `action_domains.yaml` / `caps.yaml` unchanged; settings env names
  (`AHBO_BROWSER_WS`, `AHBO_API_INTERNAL_URL`) and `domains.internal_base`'s `AHBO_ACTION_INTERNAL_BASE`
  unchanged; `kernel/wiring.py` registers no `action.run`.
- Hygiene re-checked: no secrets/keys/dialled emergency numbers in my files; no `datetime.now`/`time.time()`
  (only `time.monotonic` duration budgets, the Kernel's own pattern); no stale references to the removed
  static routes; all created/edited files stay inside `brain/ahbo/actions/**`, `brain/tests/actions/**`,
  `mock-centre/**` (untouched, verified by mtime) + the two task-sanctioned screenshot copies and this
  report.
- `tests/actions` fresh → `36 passed, 6 skipped in 10.43s` (one test added, see below).
- e2e fresh (standalone) → `6 passed in 26.11s`; `docker ps -a` / network list clean afterwards.
- `tests/kernel tests/platform tests/ops` fresh → `111 passed in 68.00s`.
- Full suite fresh (`--ignore=tests/links`) → `513 passed, 37 skipped in 378.84s` — nothing new fails
  (growth vs handoff is other agents' new tests plus the one added below).
- **Definitive full suite including `tests/links`, after the links agent's latest fixes** →
  `586 passed, 37 skipped, 1 warning in 318.79s` — the whole repo suite is green as of this check. (The
  links failures recorded above were real at handoff time while that component was mid-build; they have
  since been fixed by its owner. Nothing in my component was involved either way.)
- One test **added** during this check: `test_serving.py::test_platform_installs_the_actions_component` —
  asserts the real `create_app` app installs `ahbo.actions.wiring` (`"actions" in svc.installed`), the
  `action.run` dispatcher is registered, `mock_centre.router` is among the included component routers, and
  `mock_registrations` exists on the app's own DB. (FastAPI 0.162.2 wraps included routers in
  `_IncludedRouter` objects without `.path`, so inclusion is asserted via `svc.routers` + the endpoint
  reachability of the sibling test.)
- One hardening change during this check: the integration `settle` timeout went 30 s → 90 s. On this
  2-vCPU box an e2e run overlapped with a parallel full-suite container run once and the browser got slow
  enough that the action completed *after* the assertion window (the run finished `done`/`DEMO-0423` with
  both screenshots — the failure was the test's own timing, not the runner's). 90 s stays inside the
  runner's 110 s budget and the Kernel's 120 s guard. Lesson recorded: run `run_e2e.sh` standalone, not
  next to a parallel full-suite container.
- Exact rule-code check: `ruff check --select F,E4,E7,E9 ahbo/actions tests/actions` → all checks passed.

## What is real vs faked

Real (no money involved anywhere):
- The whole Playwright path in the e2e run: real Chromium in the `ahbo-browser` container (Playwright
  1.64.1 run-server), real HTTP to the mock site through the internal-URL rewrite, real screenshots on
  disk, real booking reference from the real `POST /mock-centre/api/register`.
- The mock centre API (also exercised through the real `create_app` with its routers).
- The cap mechanism: `ahbo.ops.caps.check_cap` + `costs` rows (provider `playwright`) — the 10/day cap
  test inserts real cost rows and the runner really refuses the 11th action.
- The Kernel flow: propose → her recorded yes (utterance + stt_confidence) → `action.run` command →
  dispatcher → `action.status` events → family NOTIFY with the reference + booked calendar event (the
  Kernel reducer's own logic, exercised for real in `test_runner.py`).

Faked / fake-mode only:
- Everything runs with `AHBO_FAKE=all`. The real-mode Sonnet field-map client exists and is unit-tested
  with a stub LLM, but was never pointed at a real API (no keys; spend rule).
- Telegram: the runner never sends anything itself; it validates the recipient against the `contacts`
  allowlist and submits the `action.message_send` Kernel event. No reducer handles that event yet, so in
  the current tree the message is recorded in the trace (INV-6) but never leaves the system (see Changes
  needed). This is by design (INV-1): a component may not send to a third party on its own.
- No real external API is called anywhere; no money spent.

## Deviations from PRD/CONTRACTS (and why)

1. **Timeout (>120 s) → `failed`, not `needs_human`.** The task text lists "more than 120 s" among the
   `needs_human` hard stops, but PRD §4.3.5 maps error/timeout → FAILED, and the Kernel reducer
   (`kernel/reducers/action.py`) already runs a 120 s `action:timeout` guard that finishes the action as
   `failed`. The runner therefore budgets itself to 110 s and reports `failed {reason: timeout}`; the
   Kernel's guard stays authoritative. All other hard stops (CAPTCHA, login, payment, unknown required
   field, cross-domain redirect, forbidden field) are `needs_human` as specified.
2. **LLM field-map proposals apply to optional fields only.** Stricter than the task text ("the result is
   validated against that allowed set"): validation of *names* cannot stop the model from mapping an
   unknown **required** field (緊急聯絡人) to a shareable one (name_zh) and getting 王婆婆 typed into it.
   Required fields must match deterministically; otherwise → `needs_human`. Fake mode (the default, and
   the demo) never calls the LLM at all.
3. **Cascading selects deferred, not rejected.** 時段 has no options until 活動 is chosen; the mapper
   defers it (`select_deferred`) and re-reads the options at fill time. If no allowed option appears, it
   is still `needs_human` (`unknown_required_field`). Without this the PRD's own demo form can never be
   filled.
4. **The mock API additionally refuses the hard-stop variants server-side** (`?variant=…` reaches the
   register endpoint as a field; `hkid/captcha/login/payment` → 400 with a Cantonese message). Defence in
   depth: even a runner bug can't book an HKID variant registration.
5. **`mock_centre.py` serves no statics** — platform already does (task text agrees; the stopped agent's
   duplicate routes were removed, one source of truth per rule 11). Added `GET /mock-centre/api/activities`
   so the page's dropdowns come from `seed/activities.yaml` (single source of truth with the runner).
6. **`action.status.screenshots` carries absolute paths** (`data/media/actions/<action_id>/*.png` as the
   task requires) — `links.family.screenshot_paths` accepts absolute paths, so Ah Chun really receives
   them; relative names would have been resolved under `media/screens` and found nothing.
7. `mock_registrations` is created at install time with `CREATE TABLE IF NOT EXISTS` and is deliberately
   **not** in `core/db.py` / `RESET_TABLES` (demo-site data, never reset with the care data) — noted for
   BUILD_LOG.

## VERIFY results (library + version + exact function names you checked)

- **Playwright**: `pip show playwright` → `Version: 1.64.1` in **both** images (`ahbo-api` client,
  `ahbo-browser` server); `playwright --version` → `Version 1.64.1`; `playwright run-server --help`
  confirms `--port` / `--host` flags. `inspect.signature(BrowserType.connect)` (from
  `playwright.async_api`, checked in both images) → `(self, endpoint: str, *, expose_network=None,
  headers=None, slow_mo=None, timeout=None) -> Browser` — the runner calls
  `pw.chromium.connect(ws, timeout=10000)`. Also verified `Browser.new_context(accept_downloads=…)`,
  `Page.goto(url, wait_until=…, timeout=…)`, `Page.wait_for_selector(sel, state=…, timeout=…)`,
  `Page.screenshot(path=…, full_page=…, type=…)`, `Locator.fill/select_option/count/text_content`.
- **`ahbo.ops.caps.check_cap`** exists (written by the other agent): signature
  `check_cap(db, clock, config, name, *, session_id=None, safety=False, incident_id=None, adding=1) ->
  (ok, used, cap)`; `playwright_actions` ∈ `CAP_NAMES`; usage is read from `costs` rows with provider
  `playwright` (`ahbo.trace.costs.spend_today`), which is why the runner records one row per attempt via
  `record_cost(db, "playwright", None, {}, 0.0, correlation_id=…, purpose="playwright_form")`.
- **`ahbo.core.numwords.hhmm_words(hhmm: str, **kw)`** (period=True) — verified via `inspect.signature`.
- **Pydantic 2.14.3**: `FormField(**js_dict)` tolerates the extra `classes` key (v2 default ignores
  extras).
- **Kernel surface**: `Kernel.on(key, fn)`, `Kernel.spawn(coro, name)`, `Kernel.submit(Event)`, and the
  `action.status` handling (accepted statuses; `done/needs_human/failed` are terminal; `filling` records
  progress) read from `brain/ahbo/kernel/core.py` and `kernel/reducers/action.py`; the dispatcher
  convention and `svc` fields per `ahbo/api/services.py` (`svc.routers` from `wiring.router`,
  `install_components` loop).
- **Ruff 0.17.4**: `ruff check --select F,E4,E7,E9 ahbo/actions tests/actions` → all checks passed (the
  repo's pyproject default rule set). The broader default-including-extra rules (BLE001 blind-except,
  FLY002) also fire on committed code by other agents (`ahbo/deep`, `ahbo/links`), so those are house
  style and left as-is.

## Changes needed in files I don't own

1. `brain/ahbo/kernel/reducers/action.py` — handle the new event **`action.message_send`** (source
   `action`, payload `{action_id, contact_id, contact_name, text, template: true}`): after re-checking the
   recipient is on the `contacts` allowlist (verified), emit
   `family.send {kind: <owner's choice, e.g. NOTIFY>, contact_ids: [contact_id], text, action_id}` so the
   templated message actually goes out through the links component (which re-checks its own allowlist and
   writes the outbox row). Until this lands, telegram-message actions complete and Ah Chun is notified,
   but the third-party message is only recorded as an event (INV-6) — nothing is sent. Diff sketch: in
   `reduce()`, `if t == "action.message_send": …` returning the one `cmd("family.send", …)`; re-use
   `_ref`/`family_contact_ids` style guards.
2. `brain/ahbo/kernel/reducers/action.py` — `_finish`'s `done` branch builds the NOTIFY text as
   「已幫王婆婆報名{title}…」 regardless of `a["kind"]`, so a `kind="message"` action reports a signup
   wording to 阿珍. Suggest branching on `a["kind"] == "message"` (e.g. 「已幫王婆婆傳咗訊息俾{title}。」).
3. `docs/BUILD_LOG.md` — entries worth recording: the `mock_registrations` table (created at install,
   outside `core/db.py` and `RESET_TABLES` on purpose); the `action.message_send` event name (CONTRACTS §4.1
   addition); the LLM-optional-only field-map rule; timeout→`failed` (matching §4.3.5, not the handoff
   text); `action.status.screenshots` carrying absolute paths under `data/media/actions/<action_id>/`.

## Known gaps / things I did not verify

- The real-mode Sonnet field map is never exercised against a real API (no keys; the spend rule). Only the
  stub-LLM unit tests cover that path.
- The cross-domain-redirect hard stop has no dedicated test (it needs a second host inside the e2e
  network). The logic is a plain host comparison of `page.url` against the navigated host after goto and
  after submit, with a `redirect.png` screenshot; untested by an actual redirect.
- `https://srv2135273.hstgr.cloud/mock-centre/` was **not** opened live (the running stack is not mine to
  touch or restart). What I verified instead: the exact same files served by the platform's own static
  router (`test_serving.py`, real `create_app`) and in real Chromium over the docker network (render
  test + the two screenshots in `docs/screens/actions/`). The page uses only relative URLs
  (`style.css`, `app.js`, `api/activities`), so it works under any mount point.
- `mock_registrations.reference` is `DEMO-%04d` of `(rowid + 422) % 10000` with a UNIQUE constraint: after
  ~9 578 registrations in one table it could collide → a 500 on insert. Demo-only; not guarded.
- Telegram `kind="message"` details may carry free text (e.g. a `message` field from the agent); by design
  it is **ignored** — the outbound text is template + structured details only. If the owner wants
  resident-approved free text sent, that needs an explicit design decision.
- Deferred cascading selects are resolved in page order (活動 precedes 時段 in the DOM, as in the mock
  form); a deferred select whose parent appears later in the DOM would hard-stop `needs_human` rather than
  fill — safe, just less clever.
- The e2e script is verified to clean up (`docker ps -a` shows no `ahbo-t2-*` leftovers), but its test
  screenshots accumulate under `data/media/actions/` (git-ignored, harmless).
- Tests were run while `tests/links` was being edited live by another agent; the links failure count
  varied between my two full-suite runs (8 vs 11). I did not investigate their failures beyond confirming
  none are caused by `ahbo.actions`.
