# GLM task 03: Operator console UI (`operator-ui/`)

Read `00_COMMON.md` first. Report goes to `docs/handoff/glm/03_REPORT.md`.

**PRD sections:** §12 (all), §13 (demo harness), §20.1–20.2 (model knobs), §20.4.5 rule 6 (wake panel), §23.1 (palette),
§26.3 (Outbox), §27.5. **CONTRACTS** §3 (face commands — for the live mirror) and §9 (the operator API you consume).

**You own:** `operator-ui/**` (entry `operator-ui/index.html`; vanilla JS / ES modules, no build step; if you use a library,
load an exact pinned version from `cdn.jsdelivr.net`), `brain/tests/operator_ui/**`.

**Already on disk from a stopped agent (review, keep what is right, finish):** `operator-ui/index.html`, `console.css`,
`js/{api,dom,mirror,state_panel,transcript,canned}.js`. No tests yet.

**The backend already exists and is live** (`brain/ahbo/api/routes/operator.py`, `api/demo.py`, `api/snapshot.py`). Read them:
they are the truth for the request/response shapes (they follow CONTRACTS §9 plus extras `POST /operator/api/reset` and
`POST /operator/api/panic`). The page is served at `/operator/` behind the operator login cookie.

## Build
1. **Live face mirror:** an iframe of `/face/index.html` that replays every SSE `ui` event with
   `iframe.contentWindow.postMessage({ahbo: cmd}, "*")`. On load, rebuild from `GET /operator/api/state` → `ui` snapshot
   (send `reset`, then mood, cards, step groups, ok_button). It must show exactly what Po-po sees. Never copy or edit `face/`.
2. **Panels (§12.1):**
   - Live state: incident, doses, actions, deep tasks, agent mode, session, arbiter queue, devices (ack latency, battery,
     unacked P0 shown red), beacons, spend vs caps.
   - Live transcript from SSE `turn` / `kernel_event`: her lines (+ STT confidence), Ah Bo's replies, tool calls collapsed,
     per-stage latency when present.
   - Triggers: 模擬跌倒, SOS, fire dose now (choose slot), fire check-in, inject voice note / family text, run 07:00 plan,
     Reset demo, Warm-up, Birth.
   - **Scenario buttons S1, S2, S3, S4, S4 OK** (`POST /operator/api/scenario/{name}`) and Prepare S1–S4 (clock anchors).
   - Overrides: mark OK / escalate / resolve, force tool result (canned results per tool), play any fixed clip
     (`GET /operator/api/fixed`), mute/unmute, say a typed line (**labelled operator**), provider fallback switches,
     interruption toggle.
   - Virtual clock: set HH:MM / offset / back to now.
   - Outbox (fake mode, clearly labelled): fake Telegram messages with photo + pressable buttons, fake calls with TwiML +
     "press 1" (`POST /operator/api/outbox/{id}/press`).
   - Health (`GET /operator/api/health` table, refresh). Model knobs (Flash thinking off/low/high/max; Sonnet
     off/low/medium/high/xhigh/max). Wake panel (show "no iPad connected" honestly when none). Trace view per correlation id
     (`GET /operator/api/trace`).
3. **Rules:** every action shows its result (ok / error); nothing edits the DB directly; **Esc = Panic**
   (`POST /operator/api/override {"kind":"panic"}` or `/operator/api/panic`) with a visible hint; fake/mock items labelled
   ("fake mode", mock centre 「示範用 Demo」); never display secrets; if an endpoint returns 404/501, show "not available yet".
4. **Look:** same dark palette and rounded font as the face (black background, ink `#ECE3E7`, dim/faint ink, amber `#FFC46B`
   only for alarms), dense readable dashboard grid; works at 1440×900 down to tablet width.
5. **Tests (`brain/tests/operator_ui/`):** Playwright (`ahbo-browser` image) against a small Python stand-in server written in
   the test, implementing the §9 endpoints with canned data and an SSE stream. Assert: panels render, buttons POST the right
   bodies, the mirror applies `ui` events (check the iframe DOM, e.g. mood class on `.face`), Esc triggers panic.
   Then a **smoke test against the real app**: on a private Docker network, run `ahbo-api` with
   `AHBO_DATA_DIR=/tmp/<x>` and env from a temp copy of the needed variables (generate a throwaway APP_SECRET/SESSION_SECRET/
   INTERNAL_TOKEN and a bcrypt OPERATOR_PASSWORD_HASH for a test password — never read or copy the real `.env`), log in,
   open `/operator/`, click **S2**, and screenshot. Save screenshots to `docs/screens/operator/`.

## Done when
- The stand-in-server Playwright tests pass, and the real-app smoke test shows the S2 cards (⏰ 12:00 食藥 + four medicine
  chips) in the mirror. Screenshots saved.
- Full suite: nothing new fails.
