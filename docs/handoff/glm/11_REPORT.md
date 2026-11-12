# GLM task 11 report — MCP connectors for Zcode (server-side)

## Files created/changed
| File | What |
|---|---|
| `/root/.zcode/cli/config.json` | **created** — Zcode user-scope MCP config (`mcp.servers`) with `playwright`, `context7`, `xcodebuild` (disabled). This is Zcode's documented config location (Zcode configuration guide: user scope = `~/.zcode/cli/config.json → mcp.servers`; strict schema; all scopes auto-connect at session start). Chosen over workspace scope so nothing lands inside the repo. |
| `/opt/ahbo/ios/MAC_BRIDGE.md` | **created** — owner's Mac setup + tunnel + sync steps (the one project file this task owns). |
| `/root/.ssh/ahbo_mac_bridge` + `.pub` | **created** — dedicated ed25519 keypair for the Mac bridge (`ssh-keygen -t ed25519 -N ""`). Private key mode 600, never leaves the server. |
| `/root/mcp-verify/*.py` | **created** (outside repo) — throwaway MCP protocol clients used for verification (stdio JSON-RPC and streamable-HTTP). |
| `/tmp/mcp-artifacts/…` | verification evidence: mock-centre screenshot PNG, exact-args run log, stderr capture. |

**Nothing was installed on the host** (no apt/pip/npm). Everything runs from pinned Docker images or a remote HTTP endpoint. No secrets were given to any MCP tool (Context7 and Playwright were used keyless; no headers carry credentials).

## Exact entries added (`/root/.zcode/cli/config.json`)
```json
{
  "mcp": {
    "servers": {
      "playwright": {
        "type": "stdio",
        "command": "docker",
        "args": ["run", "-i", "--rm", "--init", "mcr.microsoft.com/playwright/mcp:v0.0.85", "--headless", "--isolated"],
        "timeoutMs": 60000
      },
      "context7": {
        "type": "http",
        "url": "https://mcp.context7.com/mcp",
        "timeoutMs": 60000
      },
      "xcodebuild": {
        "type": "stdio",
        "command": "ssh",
        "args": ["-i", "/root/.ssh/ahbo_mac_bridge", "-p", "2222", "-o", "StrictHostKeyChecking=accept-new",
                 "<MAC_USERNAME>@127.0.0.1", "PATH=/opt/homebrew/bin:/usr/local/bin:$PATH npx -y xcodebuildmcp@2.7.0 mcp"],
        "enabled": false,
        "timeoutMs": 60000
      }
    }
  }
}
```
- Playwright tag **`v0.0.85`** = newest **versioned** tag from `https://mcr.microsoft.com/v2/playwright/mcp/tags/list` (excluded `latest`, canary and `-amd64/-arm64` arch tags). Image pulled, digest `sha256:91838ad4…`, image ID `91838ad4927e`.
- Flags: `--headless` (required — the server has no display) and `--isolated` (in-memory browser profile; keeps sessions clean) — both confirmed against the image's own `--help`. `--no-sandbox` was **not** needed: Chromium launches fine in this container as tested.
- `xcodebuild` is added **disabled** with the `<MAC_USERNAME>` placeholder; Zcode supports `enabled: false`, so this satisfies "add it disabled".

## Verification (what each actually returned)
All verifications were done by speaking MCP to the servers exactly as configured (stdio JSON-RPC / streamable HTTP — what Zcode itself does). A **running** Zcode session does not hot-reload MCP config, so these servers connect at the **next session start**; owner can confirm under **Settings → MCP**.

**1. Playwright MCP** — run with the exact configured args (`docker run -i --rm --init mcr.microsoft.com/playwright/mcp:v0.0.85 --headless --isolated`):
- `initialize` → server `Playwright version=1.66.0-alpha-2026-11-09`, protocol `2025-06-18`; `tools/list` → 25 tools (`browser_navigate`, `browser_evaluate`, `browser_take_screenshot`, `browser_console_messages`, `browser_close`, …).
- `browser_navigate https://srv2135273.hstgr.cloud/healthz` then `browser_evaluate (() => document.body.innerText)` returned:
  `{"ok":true,"version":"0.1.0","fake":["livekit","models","speech","telegram","twilio","vision"],"profile":"demo"}`
- `browser_navigate https://srv2135273.hstgr.cloud/mock-centre/` → page loads, title `活動報名｜秀茂坪長者中心（示範）`; `browser_take_screenshot` returned a PNG (saved to `/tmp/mcp-artifacts/mock-centre/mock_centre_1.png`; visually confirmed: demo banner, centre header, 報名表單 form rendering correctly).
- Console check: the single console error on each page is `404 @ /favicon.ico` — cosmetic, project-side, not fixed (not my file).

**2. Context7 (remote HTTP)** — `initialize` → server `Context7 version=4.5.0`, protocol `2025-06-18`; tools `resolve-library-id` (required: `query`, `libraryName`) and `query-docs` (required: `libraryId`, `query`; optional: `language`).
- `resolve-library-id {libraryName:"livekit-agents"}` → resolved `/livekit/agents` (LiveKit Agents, 3294 snippets, high reputation).
- `query-docs {libraryId:"/livekit/agents", query:"AgentSession", language:"python"}` returned real docs; one line from the answer: *"AgentSession is the LiveKit Agents runtime that glues together media streams, speech/LLM components, and tool orchestration into a single real-time voice agent."* — followed by the actual `class AgentSession(...)` constructor signature.

**3. Chrome DevTools MCP — SKIPPED.** No official Docker image exists; the maintainers state "We currently do not plan to provide a docker image" ([ChromeDevTools/chrome-devtools-mcp discussion #749](https://github.com/ChromeDevTools/chrome-devtools-mcp/discussions/749)). Standard install is host-side `npx`, which is forbidden here. Building a custom Chrome+Node image would violate the "official pinned image" rule, so per the task it is skipped — Playwright MCP covers the same needs (it even exposes a `devtools` capability via `--caps devtools`, and `browser_console_messages` / `browser_network_requests` worked above).

**4. XcodeBuildMCP bridge (prepare only)**
- Bridge keypair created; **public key for the owner** (also embedded in `ios/MAC_BRIDGE.md`):
  ```
  ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBVbnUakGGbdrz3MpMG+3nktCODw//uoGQGzGWMW4XYW ahbo-mac-bridge (ZCode XcodeBuildMCP)
  ```
- Version check: npm registry `dist-tags` for `xcodebuildmcp` → **`latest` = `2.7.0`** (also `temp: 26.0.0`, `prerelease: 1.11.0`, `beta: 2.5.0-beta.1` — ignored; 2.7.0 is the current latest stable of this package, matching the task pin).
- Launch-command check: ran `npx -y xcodebuildmcp@2.7.0 --help` inside a throwaway pinned `node:22-slim` container (no host install). Output: *"Run 'xcodebuildmcp mcp' to start the MCP server."* → the task's draft entry was **corrected to append the `mcp` subcommand**; bare invocation only prints help and exits. No required env vars (per README/docs; the `PATH=…` prefix just makes Homebrew's `npx` reachable in the non-interactive SSH session).
- Bridge refusal check (tunnel not open): `ssh -i /root/.ssh/ahbo_mac_bridge -p 2222 -o BatchMode=yes macuser@127.0.0.1 true` → `ssh: connect to host 127.0.0.1 port 2222: Connection refused`, exit 255 — clean failure as expected; nothing listens publicly, the tunnel binds loopback only when the Mac opens it.
- End-to-end MCP-over-SSH is **not** verified (needs the owner's Mac) — by design of the task.

## Tests (exact command + the real last line of pytest output)
No project code was touched by this task, so no new tests were added. Verification outputs are quoted above.

## Full-suite run (last line)
`docker run --rm -v /opt/ahbo:/repo ahbo-api python -m pytest -q`
```
1 failed, 612 passed, 37 skipped, 1 warning in 225.08s (0:03:45)
```
The 1 failure is `tests/actions/test_runner_browser_unavailable.py::test_domain_checks_still_run_before_the_browser` — the known failure named in `00_COMMON.md` (task 06 fixes it; not mine). My changes (repo-external config + `ios/MAC_BRIDGE.md`) cannot affect pytest.

## What is real vs faked
All real: real Docker image pulled from MCR; real MCP stdio/HTTP protocol sessions; real page loads over public HTTPS; real screenshot; real Context7 doc lookup; real npm registry and real container check of xcodebuildmcp 2.7.0. No fakes, no mocks, no money spent (Context7 free keyless tier; MCR/Docker Hub/npm are free).

## Deviations from the task (and why)
1. **Chrome DevTools MCP skipped** — task explicitly allows this when no official image exists (it doesn't).
2. **`xcodebuild` entry corrected**: added the `mcp` subcommand after `npx -y xcodebuildmcp@2.7.0` (2.7.0's CLI requires it — verified in-container). The task's draft would not start the server.
3. **Playwright entry adds `--isolated`** and `timeoutMs` — hygiene (in-memory profile) and headroom for Docker cold starts; the exact configured command was re-verified end-to-end afterwards.
4. **Verification method**: done via direct MCP protocol sessions instead of through a live Zcode session, because MCP config is read at session start and this session predates the config. Same wire protocol Zcode speaks.

## VERIFY results (versions + exact names checked)
- Zcode config surface: user scope `~/.zcode/cli/config.json` → `mcp.servers`; stdio keys `command`/`args`/`cwd`/`env`/`enabled`/`timeoutMs`; http keys `url`/`headers`/`enabled`/`timeoutMs`; `type` inferred but set explicitly; strict schema (unknown keys drop the server) — from Zcode's own configuration/diagnosis skills.
- `mcr.microsoft.com/playwright/mcp:v0.0.85`: self-reports `Playwright 1.66.0-alpha-2026-11-09`, MCP protocol `2025-06-18`; tools used: `browser_navigate`, `browser_evaluate` (param `function`), `browser_take_screenshot`, `browser_console_messages`, `browser_close`; flags confirmed from its `--help`: `--headless`, `--isolated`, `--no-sandbox` (unused), `--caps` (has `devtools`).
- Context7 remote `https://mcp.context7.com/mcp`: `Context7 4.5.0`; tools `resolve-library-id` (`query`, `libraryName`), `query-docs` (`libraryId`, `query`, `language`); library ID resolved: `/livekit/agents`.
- `xcodebuildmcp@2.7.0` (npm `latest`): bins `xcodebuildmcp`→`build/cli.js`, `xcodebuildmcp-doctor`→`build/doctor-cli.js`; `--help` shows subcommand `mcp` ("Start the MCP server (for use with MCP clients)").
- sshd refusal: `Connection refused` on `127.0.0.1:2222` (exit 255).

## Changes needed in files I don't own
None. (One cosmetic project-side observation, no change requested: `/favicon.ico` 404s on the public pages.)

## Owner's remaining manual steps
1. **Restart Zcode / start a new session**, open **Settings → MCP**: `playwright` and `context7` should show *connected*; `xcodebuild` *disabled*.
2. Follow `/opt/ahbo/ios/MAC_BRIDGE.md`: Xcode 26 + CLT, `brew install node xcodegen`, Remote Login on, append the bridge public key (above) to `~/.ssh/authorized_keys`, rsync `ios/`, `face/`, `brain/config` to `~/ahbo/`, `xcodegen`, test `npx -y xcodebuildmcp@2.7.0 --help`.
3. While doing iOS work, open the tunnel on the Mac: `ssh -N -R 127.0.0.1:2222:localhost:22 root@srv2135273.hstgr.cloud` (leave the window open).
4. Then edit the `xcodebuild` entry: replace `<MAC_USERNAME>` with the macOS username and set `"enabled": true`; the working check is asking Zcode to list schemes for `~/ahbo/ios/AhBo.xcodeproj` and build the `AhBo` scheme for an iOS Simulator.
5. Optional (not created, per task): to let Playwright MCP test containers on a private build network such as `ahbo-t-g3`, a second entry can add `"--network", "ahbo-t-g3"` to the `docker run` args (create the network only when needed).

## Known gaps / things I did not verify
- The three servers are **not loaded into the current session** (config is read at session start); the MCP-status page in Zcode is the owner's confirmation point.
- `xcodebuild` end-to-end (SSH → Mac → XcodeBuildMCP) is untested until the Mac side exists; `PATH` assumes Apple Silicon Homebrew plus `/usr/local/bin` fallback — an Intel-only Homebrew at another path would need adjusting.
- Context7 is used keyless (free tier); if the owner later adds an API key it must go in `headers`, never in `url` or code.
- Screenshot/snapshot files the MCP writes inside its container (`.playwright-mcp/…`) are ephemeral (`--rm`); durable artifacts were pulled out via tool results only — no host volumes are mounted into the Playwright container (it has no access to `/opt/ahbo`).
- The npm `temp: 26.0.0` dist-tag on `xcodebuildmcp` (site renaming itself to "MobileBuildMCP") was noticed and ignored — the task pins `2.7.0`, which is the current `latest` of this package.

## Addendum (11 Nov, later): xcodebuild made actually working — end-to-end verified

**Failure seen in the Zcode log** (`zcode-2026-11-11.jsonl` 02:41:19): `mcp.server.failed, error "Connection closed", stderr "remote username contains invalid characters"` — two causes stacked:
1. The `<MAC_USERNAME>` placeholder was never replaced, and a Settings → MCP edit had removed `"enabled": false`, so Zcode tried (and failed) to start the entry every session.
2. The Mac reverse tunnel was not open (nothing listening on `127.0.0.1:2222`).

**Fixed, in order:**
1. Config rewritten: real username **`judeliu`** (owner's Mac `Judes-MacBook-Pro-33138`, Apple Silicon, macOS 26.6.1), `-o BatchMode=yes` added (fail fast instead of hanging on prompts), remote command kept as one string; entry temporarily disabled while diagnosing, **enabled at the end**.
2. Owner enabled Remote Login and opened the tunnel; `ssh -p 2222` bridge verified (`BRIDGE-OK`, arm64).
3. Exact remote command verified on the Mac: `npx -y xcodebuildmcp@2.7.0` runs (node v26.13.0 via Homebrew; the entry's PATH puts `/opt/homebrew/bin` first, which matters because `/usr/local/bin` shadows it in interactive shells).
4. MCP handshake over the bridge: `xcodebuildmcp 2.7.0`, protocol `2025-06-18`, 24 tools (`list_schemes`, `build_sim`, `build_run_sim`, `list_sims`, `session_set_defaults`, `screenshot`, …).
5. **Xcode was installed but not selected** (`xcode-select -p` → CommandLineTools; `xcodebuild` errored "requires Xcode"). Owner ran `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer` + `sudo xcodebuild -runFirstLaunch` → **Xcode 27.0 (27A266a)**, iOS sim runtimes 26.5 + 27.0.
6. `AhBo.xcodeproj` had been deleted on the Mac by the owner's second `rsync --delete` (it exists only on the Mac) — regenerated with `xcodegen` over the bridge; the doc now warns to re-run `xcodegen` after every sync.
7. **AhBo is iPad-only**: `-showdestinations` lists every iPhone sim as incompatible ("doesn't match any of AhBo.app's targeted device families") — building with an iPhone destination fails, which is the expected next trap. Correct target: iPad sims.
8. **End-to-end build succeeded** via the bridge MCP tools: `list_schemes(projectPath:"/Users/judeliu/ahbo/ios/AhBo.xcodeproj")` → `AhBo`; `session_set_defaults{scheme:"AhBo", simulatorId:"B3C5E078-F1F3-4F24-BBA5-12B0F8DB1D9D"}` (iPad Pro 13-inch M5, iOS 27.0); `build_sim` → **"✅ Build succeeded. (⏱️ 33.3s)"**, one benign warning (`UIRequiresFullScreen` deprecated in iOS 26). Evidence: `/tmp/mcp-artifacts/xcode_build_result3.txt`.

**Remaining for the owner:** none for setup — for daily use, open the tunnel each session (`ssh -N -R 127.0.0.1:2222:localhost:22 root@srv2135273.hstgr.cloud`), re-run `xcodegen` after each rsync, and build on an iPad simulator. When the tunnel is closed the enabled entry fails fast with "connection refused" — that is the designed state, not an error to fix.
