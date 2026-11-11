# SETUP — what the owner provides, and what is already done

Status as of **12 Nov 2026** (checked with `docker exec ahbo-api-1 python -m ahbo.ops.doctor`).
✅ working · ⬜ needs you · ➖ not needed. Keys live only in `/opt/ahbo/.env` (mode 600, git-ignored). Never paste keys
into git, the face, the iPad or chat logs you share.

After you change `.env`: keep `chmod 600 /opt/ahbo/.env`, then
```bash
cd /opt/ahbo/deploy && docker compose -p ahbo up -d --force-recreate api agent
```
```bash
docker exec ahbo-api-1 python -m ahbo.ops.doctor
```
Every row turns green or says exactly what is wrong.

## 1. Accounts and keys

| # | Service | Status | `.env` lines | Notes |
|---|---|---|---|---|
| 1 | **Opper** (all AI models: DeepSeek V4.1 Flash, Claude Haiku 5.5, Claude Sonnet 5.5) | ✅ live | `OPPER_API_KEY=` | Project key (not `op-mak-…`). Model ids in `brain/config/models.yaml`. Consider rotating: the key was pasted in chat. |
| 2 | DeepSeek / Anthropic direct keys | ➖ | `DEEPSEEK_API_KEY=` `ANTHROPIC_API_KEY=` | Not used (Opper covers both). Leave empty. |
| 3 | **ElevenLabs** (listening = Scribe v3 live, voice = CY – Calm & Friendly) | ✅ live | `ELEVENLABS_API_KEY=` `ELEVENLABS_VOICE_ID=SPRXwABrRtJGzRFBpO1H` | Starter plan. Key permissions: Text to Speech, Speech to Text, Voices (read), Models, User; IP-restricted to this VPS. Models in `models.yaml → speech:` (`eleven_v4_turbo` live, `eleven_v4` safety lines). |
| 4 | Azure Speech | ➖ | `AZURE_SPEECH_KEY=` | Replaced by ElevenLabs (sign-up was blocked). Optional fallback only. |
| 5 | **LiveKit Cloud** (voice transport) | ✅ live | `LIVEKIT_URL=` `LIVEKIT_API_KEY=` `LIVEKIT_API_SECRET=` | Voice worker `ahbo-agent` registered. Consider rotating the secret (shown in a screenshot). |
| 6 | **Telegram bot** `@ahbo_silverlining_bot` | ✅ live | `TELEGRAM_BOT_TOKEN=` `FAMILY_TELEGRAM_CHAT_IDS=阿珍:<chat id>` | Webhook set; 阿珍's chat is the caregiver + admin. To add/replace 阿珍: that phone sends `/whoami` to the bot, put the id in `.env`, recreate. Consider `/revoke` in @BotFather (token was pasted in chat). |
| 7 | **Twilio** (phone call to 阿珍 on a real fall) | ⬜ **real, but number not verified** | `TWILIO_ACCOUNT_SID=` ✅ `TWILIO_AUTH_TOKEN=` ✅ `TWILIO_FROM=+17372508034` ✅ `FAMILY_PHONE=+85297298627` ✅ | Trial account, balance $0. **You:** (a) Console → Phone Numbers → Buy a number (trial: one US number with Voice from trial credit) → `TWILIO_FROM=+1…`; (b) Verified Caller IDs → add 阿珍's phone; (c) put 阿珍's number in Telegram **聯絡人 → 改聯絡人** (or `FAMILY_PHONE=+852…`); (d) approve a top-up when you want calls without the trial announcement; (e) remove `twilio` from `AHBO_FAKE`. 999/112/911 are refused in code. |
| 8 | Emergency centre number | ➖ | `EMERGENCY_SIM_PHONE=` | Owner decision 11 Nov: falls call 阿珍 only. |
| 9 | Beacon (pocket fall sensor) | ⬜ optional | `BEACON_TOKENS=beacon1:<token>` | Without it, use Telegram **示範控制 → 模擬跌倒** or `/fall`. |
| 10 | **Website login** (`https://srv2135273.hstgr.cloud/login`, protects `/sim/`) | ✅ owner's password set 12 Nov | `OPERATOR_PASSWORD_HASH=` | Change it any time: `/opt/ahbo/deploy/make.sh set-password`. |
| 11 | App secret (for the iPad) | ✅ generated | `APP_SECRET=` | Shown once with `/opt/ahbo/deploy/make.sh show-app-secret`; typed into the iPad's hidden settings. |

`AHBO_FAKE` today: `none` — everything is real. Open doctor items: Twilio caller-ID verification of 阿珍's phone; no iPad online yet; no beacon (optional).

## 2. Decisions only you can make
- [ ] The voice for the recorded safety lines: listen to `evals/audio/tts_eleven_v4.mp3` (chosen) vs `tts_eleven_v3.mp3`;
      to switch, change `speech.tts.fixed_model` in `brain/config/models.yaml` (clips re-record on restart).
- [ ] A real recording for 阿珍's voice note (send a voice message to the bot from 阿珍's phone).
- [ ] Approve running the eval suites against the real AI (< $1 on Opper).
- [ ] The grand-final date.

## 3. The iPad app (needs your Mac) — ⬜ not built yet
Follow `ios/BUILD_ON_MAC.md` (Xcode 26, `brew install xcodegen`, `cd ios && xcodegen && open AhBo.xcodeproj`, Personal
Team signing, Simulator first, then the iPad Air 5 on iPadOS 26). `ios/MAC_BRIDGE.md` describes the optional remote
build bridge. The app has **never been compiled**; expect a few compile fixes (listed in BUILD_ON_MAC.md §7).
Until then, `https://srv2135273.hstgr.cloud/sim/` (browser iPad, "Talk (browser mic)") is the full face + voice.

## 4. How to try everything today
1. Telegram → `@ahbo_silverlining_bot` → `/menu` (status, medicines, calendar, settings, contacts, demo controls).
2. Laptop Chrome → `/sim/` → log in → "Talk (browser mic)" → say 「阿寶，我幾時食藥呀？」.
3. Telegram `/scenario S1` … `/scenario S4ok` plays the demo scenarios on `/sim/`; `/fall` runs a real fall drill
   (Telegram alert is real, the call stays simulated until Twilio is set up).
4. `docker exec ahbo-api-1 python -m ahbo.ops.doctor --latency` shows conversation speed (target p50 ≤ 3.0 s, p95 ≤ 4.5 s).
