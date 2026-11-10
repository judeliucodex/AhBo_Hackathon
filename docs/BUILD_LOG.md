# Ah Bo build log

Decisions, deviations from the PRD (with reasons), VERIFY results and open flags.
Newest entries are appended at the end of each section.

## 1. Server recon (10 Nov 2026, before any change)

| Item | Finding |
|---|---|
| OS | Ubuntu 24.04.5 LTS (noble), kernel 6.8.0-149 |
| CPU / RAM / disk | 2 vCPU · 7.8 GiB RAM (no swap) · 96 GB disk, 94 GB free |
| Docker | Engine 29.9.3 · Compose v5.7.0 |
| Containers | none (`docker ps -a` empty) |
| Listening ports | 22 (sshd), 53 (systemd-resolved, loopback), 127.0.0.1:65529 (monarx-agent, the host's security agent). **80 and 443 free.** |
| ufw | active; default deny in; allow 22, 80, 443 (v4+v6). Not changed. |
| Host Python | 3.12.3, no `venv`/`ensurepip` → all Python runs in containers. No node, no uv on the host. |
| DNS | `srv2135273.hstgr.cloud` → 72.60.232.111 publicly (= this host). Locally `/etc/hosts` maps the hostname to 127.0.1.1. |
| Git | 2.43; repo initialised in place at `/opt/ahbo`, branch `main`, local identity `Ah Bo Builder <builder@ahbo.invalid>` (no owner noreply address given). No remote. |

Nothing on the host's security configuration (sshd, ufw, fail2ban, users) is touched by this build.

## 2. Kit

- `ui-reference/face/*` copied byte for byte into `face/` (`diff -r` clean). `ui-reference/` stays read-only.
- Storyboards run headlessly (Playwright 1.64.1, Chromium, 1600×1000 viewport) by `evals/visual/kit_capture.py`:
  - golden traces → `brain/tests/golden/kit/{S1,S2,S3,S4,S4ok}.json` (21/19/36/25/26 commands);
  - keyframe screenshots → `docs/screens/kit/` (45 PNGs). These are the visual bar.
- The headless Chromium shows the kit's own font fallback (M PLUS Rounded 1c lacks some Han glyphs, so a few characters render in the fallback font). That is the kit as-is; not "fixed".

## 3. Decisions and deviations

| # | Decision | Why |
|---|---|---|
| D-1 | Shared contracts live in `brain/ahbo/core/` (events, db schema, settings, config loader, virtual clock, ids, Cantonese number words, adapter protocols). `kernel/clock.py` re-exports the clock. | One frozen place for what every component imports; lets components be built in parallel without editing each other's files. |
| D-2 | The repo is bind-mounted into the `api` container (`/repo`) instead of baking code into the image. | The PRD's "one environment on the VPS" (§26.1): code changes need a restart, not a rebuild. Dependencies are baked into the image. |
| D-3 | `/face/`, `/sim/`, `/operator/` and `/mock-centre/` static files are served by `api` (behind Caddy), not by Caddy's file_server. | One place for the auth rules (§26.4); Caddy stays a pure TLS proxy that blocks `/internal/*`. |
| D-4 | Initial operator password: `ahbo init-secrets` generates a random one, stores only its bcrypt hash in `.env`, and writes the plaintext once to `data/secrets/operator_password.initial` (mode 600, git-ignored) so the owner can read it over SSH (`make show-initial-password`) and replace it with `make set-password`, which deletes that file. | M0 requires the owner to be able to log in before they have run anything; PRD §26.2 says the plaintext is never stored — this is the one bounded exception, removed by `set-password`. Headless checks read the same file. |
| D-5 | `play_voice_message` has no entry in `ui_steps.yaml`. | The kit's S1 storyboard (the golden trace, which wins on visuals) shows no playback step; the playing waveform card is the visual. |
| D-6 | Mood `listening` = a session was just opened by wake/touch or **she is speaking**. While the agent merely waits after a reply the face is `idle`. | The kit storyboards return to `idle` after every reply and only show `listening` while she speaks (§23.3 row 6 refined to match the kit). |
| D-7 | Turn cards are flushed when the reply starts (mood → cards → caption), not when each tool returns. | Matches the kit order (step ✓ → mood → cards → caption) in S1, S2, S3. |
| D-8 | Fall: `ok_button` + `countdown` are sent after F3 finishes (playback_done or its max duration), and the Kernel's N-second timer starts then. | Matches the kit S4 order (F3 caption, then button and countdown). |
| D-10 | INV-10's "at most 2 sentences" counts sentences longer than 8 characters; shorter ones (「有呀！」「想唔想去？」「到時我會提你㗎。」) are tags and don't count. | The owner-confirmed S2 and S3 scripts (§1.3) have three sentence marks each; this keeps them verbatim while still cutting long rambles. |
| D-9 | `activities.yaml` gains `venue_short` (e.g. 長者中心) and the calendar seed is used as-is; card labels use 12-hour `h:mm` (3:00, 9:30, 12:30). | The kit cards read 「3:00 粵曲班」/「長者中心 · 可代報名」. Additive only. |

## 4. VERIFY results

| Item | Result | How verified |
|---|---|---|
| Playwright Python | 1.64.1 used for both the client (api) and the `browser` image `mcr.microsoft.com/playwright/python:v1.64.1-noble` (latest image tag on MCR; PyPI has 1.65.0 but client/server must match). The image ships browsers but **not** the Python package; it is pip-installed in `Dockerfile.browser`. | MCR tag list, PyPI JSON, running the capture |

## 5. Open flags

- (none yet)

## 6. Build organisation (owner decision, 10 Nov)

Because of credit limits, the remaining component work is split. Claude Code builds the safety-critical and tightly coupled
components (Kernel flows, Agent + models + deep, Links (Telegram/Twilio/INV-9), Browser iPad). GLM 5.3 Flash in Zcode builds
three self-contained ones from hand-off prompts in `docs/handoff/glm/`: Ops/speech/trace, Actions + mock centre, Operator
console. Every GLM deliverable is reviewed, tested and committed by Claude before it counts toward a gate.
