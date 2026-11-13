# Ah Bo (阿寶)

Ah Bo is a Cantonese-speaking companion for an older person living alone in Hong Kong ("Po-po", 王婆婆 in the
demo). An iPad shows Ah Bo's face, listens for 「喂阿寶」 and talks with her in colloquial Cantonese. Behind it, a
deterministic **Care Kernel** on a VPS owns everything safety-critical: medicine reminders, fall and SOS
incidents (fixed pre-recorded audio, a 「我冇事」 button, then a Telegram alert and phone calls to verified
contacts only), daily check-ins and the family link. Language models help with conversation, memory and planning,
but can never lower a safety state, give dosing advice or dial a number. Everything spoken to her is HK Cantonese
in Traditional characters. The four demo scenarios are S1 (a voice note from her daughter 阿珍), S2 (「我幾時食藥呀？」),
S3 (planning the day and signing up for an activity) and S4 (a fall, with the S4-OK branch).

## Architecture

| Part | Where | What it does |
|---|---|---|
| Care Kernel | `brain/ahbo/kernel` | Single-writer event loop and reducers (fall, dose, concern, check-in, action, arbiter, scheduler). Every input is an event, every output a command; both are traced. |
| Agent and models | `brain/ahbo/{agent,models,deep}` | Turn loop (text loop in fake mode, LiveKit worker for real voice), tools, spoken filter, DeepSeek Flash, the Sonnet deep agent, vision. |
| Memory | `brain/ahbo/memory` | Facts, session summaries, life stories, repeated-question cards, retention. |
| Links | `brain/ahbo/links` | Device Hub (`/ws/device`), Telegram family link, Twilio phone link with the INV-9 dial guard, beacon sensors, outbox. |
| Actions | `brain/ahbo/actions`, `mock-centre/` | Playwright sign-up on the demo registration site after her spoken yes. |
| Platform and ops | `brain/ahbo/{api,ops,speech,trace}`, `deploy/` | FastAPI app, login, operator API, fixed-clip rendering, doctor, caps, backups; Docker Compose with Caddy as the only public service. |
| Faces and consoles | `face/` (kit, unmodified), `sim/`, `operator-ui/`, `ios/` | The face, the browser iPad, the operator console, the native iPad app (SwiftUI). |

## Run it (on the VPS, from `/opt/ahbo`)

```sh
deploy/make.sh up          # build and start the stack (fake mode, AHBO_FAKE=all, $0)
deploy/make.sh demo-fake   # reset the demo and print the URLs
# then open https://srv2135273.hstgr.cloud/operator/ and /sim/ side by side, log in, press S1–S4
```

The host has no `make`; `deploy/make.sh <target>` runs the Makefile in a container (BUILD_LOG D-11). The initial
operator password is in `data/secrets/operator_password.initial` (`deploy/make.sh show-initial-password`); replace
it with `deploy/make.sh set-password`. Tests: `deploy/make.sh test`. Evals: `deploy/make.sh eval` →
`evals/report.md`. Health table: `deploy/make.sh doctor`.

## Documents

| File | For |
|---|---|
| [`SETUP.md`](SETUP.md) | The owner's checklist: keys, accounts, decisions, the iPad (generated: `python -m ahbo.ops.setup_md`) |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | Demo day, recovery, logs, traces |
| [`PRD_ahbo.md`](PRD_ahbo.md) | Behaviour and safety (source of truth) |
| [`docs/CONTRACTS.md`](docs/CONTRACTS.md) | Interfaces between the parts |
| [`docs/BUILD_LOG.md`](docs/BUILD_LOG.md) | Decisions, deviations, VERIFY results, open flags |
| [`docs/FULL_GUIDE.md`](docs/FULL_GUIDE.md) | Build plan, gates, current status |
| [`ios/BUILD_ON_MAC.md`](ios/BUILD_ON_MAC.md), [`ios/API_VERIFICATION.md`](ios/API_VERIFICATION.md) | Building the iPad app |
| [`evals/report.md`](evals/report.md) | Latest eval results |

## Status (11 Nov 2026)

- **Runs in fake mode only.** No API keys exist yet; nothing has ever called a paid service, sent a real message
  or placed a call. SETUP.md lists what the owner must add.
- **Real (running on the VPS today):** the Kernel and every safety flow, the device protocol, the browser iPad and
  face, the operator console, memory (SQLite), the outbox, the Playwright sign-up against the mock centre (in the
  `browser` container), login and TLS. Full test suite: 586 passed, 37 browser-gated skips.
- **Faked (`AHBO_FAKE=all`):** the conversation model (a rule-based Cantonese FakeFlash calling the real tools),
  the deep agent (seeded results), vision, speech (silent clips with estimated captions), LiveKit (text sessions
  from the browser iPad's text box), Telegram and Twilio (rows in the console's Outbox, with pressable buttons).
- **Written but never run:** the real adapters (mock-tested), the LiveKit voice worker, and the iPad app (never
  compiled with Xcode; see `ios/BUILD_ON_MAC.md`).
- Evals in fake mode: safety red-team 41/41, medicine truth 14/14, memory 15/15, Cantonese 40/40, escalation
  21/21, scenarios 8/9 (S3 sign-up needs the `browser` service). Open items: `docs/BUILD_LOG.md` §5.
