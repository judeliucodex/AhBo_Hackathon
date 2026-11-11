# QA 24: Platform, device channel, security

Read `20_QA_COMMON.md` first. Report: `docs/handoff/glm/24_REPORT.md`. Evidence: `docs/qa/24/`.

**Spec:** PRD §10 (device contract), §11, §26.4 (endpoints + auth), §24.13–24.14; CONTRACTS §2, §5, §8, §9, §14;
GOD_PROMPT hard rules 6 and 8. **Code:** `brain/ahbo/api/**`, `brain/ahbo/links/device_hub.py`, `deploy/{compose.yaml,
Caddyfile}`, `brain/ahbo/ops/{secrets,cli}.py`.

## Check on your throwaway server
1. **Auth matrix:** every route in CONTRACTS §8–9 and §14 without a cookie, with a wrong cookie, with a device token,
   with the operator cookie. Only `/healthz`, `/face/*`, `/mock-centre/*`, `/login`, provider webhooks are public. Login
   rate limit (5/min/IP); cookie flags; `/internal/*` needs `INTERNAL_TOKEN`.
2. **Device channel, scripted fake iPad** (Python `websockets` client, `?sim=1` with the cookie, and Bearer APP_SECRET):
   wrong auth → close 4401; hello → welcome fields; replay after welcome; `ack` handling; P0 resend ≤3× if not acked;
   commands de-duplicated by id on your side; step `done` held ≥600 ms after `active`; two devices at once (both get
   commands; `playback_done` de-duplicated); disconnect 30 s → `device.offline`; `utterance` frames start text turns.
3. **Restart recovery:** start a fall, `docker restart ahbo-qa24-api`, reconnect the fake iPad: does the face come back
   (replay) and does the incident continue sensibly? Reset demo via the API: time it (≤ 5 s).
4. **Secrets:** grep your server's logs, `/operator/api/state`, `/operator/api/health`, `/operator/stream` output and
   Telegram `/health` text for anything that looks like a key or token (more than the last 4 chars) — S1 if found.
5. **Webhooks:** `/telegram/webhook` without/with wrong secret; `/twilio/*` without a valid `X-Twilio-Signature`;
   `/events/sensor` without/with a wrong `X-Beacon-Token`, duplicate `(beacon_id, seq)`.
6. **Input abuse:** oversized JSON, wrong types, unicode garbage, path traversal on `/media/*`, huge photo upload.

## Read-only checks on the LIVE host (only these, nothing else)
`curl -s -o /dev/null -w '%{http_code}'` for: `https://srv2135273.hstgr.cloud/healthz` (200),
`/internal/turn`, `/INTERNAL/turn`, `/%69nternal/turn` (all must be 404), `/operator/api/state` and `/sim/` without a cookie
(401/303), `/ws/device` without auth (must not upgrade). Also `ss -ltnp` on the host: only Caddy on 0.0.0.0:80/443; every
other `ahbo` port bound to 127.0.0.1. Never POST to the live host.
