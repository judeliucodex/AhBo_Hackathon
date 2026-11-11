# GLM task 11: Set up MCP connectors for testing (Zcode on the Ubuntu server)

Read `00_COMMON.md` first. Report goes to `docs/handoff/glm/11_REPORT.md`. This task changes **Zcode's own MCP
configuration**, not the project. Don't edit project code. Don't run git.

## Ground rules for this task
- **Nothing is installed on the host with apt, pip or a global npm install.** Every MCP server runs either as a Docker
  container started by Zcode (`docker run -i --rm …`) or as a remote HTTP endpoint. Pin exact versions/tags, never `latest`.
- Find **Zcode's own documented way to add MCP servers** (its settings UI or its config file). Don't guess a file path: look
  for Zcode's docs/help or an existing config. If it accepts a standard `mcpServers` JSON block, use the snippets below
  adapted to its format. If you can't find how, stop and write in your report exactly what the owner must click.
- Never give an MCP tool the operator password, `.env` values or any key. Test only **public** pages:
  `https://srv2135273.hstgr.cloud/healthz` and `https://srv2135273.hstgr.cloud/mock-centre/`.
- Never touch ufw, sshd config, users, or the running `ahbo` containers.

## 1. Playwright MCP (browser automation) — install and verify
- Official image `mcr.microsoft.com/playwright/mcp`. List tags with
  `curl -s https://mcr.microsoft.com/v2/playwright/mcp/tags/list` and pin the newest **versioned** tag (e.g. `v0.0.NN`).
  `docker pull` it.
- MCP server entry (stdio through Docker):
  ```json
  "playwright": { "command": "docker",
    "args": ["run", "-i", "--rm", "--init", "mcr.microsoft.com/playwright/mcp:<TAG>"] }
  ```
  Check the image's own help (`docker run --rm mcr.microsoft.com/playwright/mcp:<TAG> --help`) for the headless/browser
  flags it needs and add them if required.
- For testing against the private Docker networks the build tasks create (e.g. `ahbo-t-g3`), a second entry may add
  `"--network", "<that network>"` — note it in the report; don't create networks here.
- **Verify:** from Zcode, use the Playwright MCP to open `https://srv2135273.hstgr.cloud/healthz` and read the JSON, then
  open `/mock-centre/` and take a screenshot. Paste the results.

## 2. Context7 (library documentation lookup) — remote, nothing to install
- Remote MCP endpoint `https://mcp.context7.com/mcp` (HTTP transport). Add it the way Zcode adds remote/HTTP MCP servers.
  If Zcode only supports stdio, skip it and say so (don't install a Node bridge on the host).
- **Verify:** ask it for the `livekit-agents` (Python) docs on `AgentSession` and paste one line of the answer.

## 3. Chrome DevTools MCP (optional; console errors / network inspection)
- Only if it can run from a pinned Docker image without installing anything on the host. If no official image exists,
  skip it and say so — Playwright MCP covers most of the same needs.

## 4. XcodeBuildMCP (iPad app build) — prepare only; it runs on the owner's Mac
XcodeBuildMCP (npm `xcodebuildmcp`, pin `2.7.0` unless a newer exact version is current) needs macOS + Xcode; it cannot run
on Ubuntu. The project files stay on this server; the Mac is a **build mirror**:
- **Source of truth stays on the server.** The Mac gets a one-way copy of `ios/`, `face/`, `brain/config/` via rsync from
  the server; nobody edits the Mac copy (edits are made on the server, then re-synced).
- Zcode on the server reaches the Mac's XcodeBuildMCP over SSH (stdio over SSH). Because the Mac is usually behind home
  NAT, the **Mac opens a reverse SSH tunnel to the server** (no VPN, no firewall change on the server: the tunnel binds
  to `127.0.0.1` on the server only).

Do on the server (prepare, don't enable yet):
1. Create a dedicated SSH key for this bridge: `ssh-keygen -t ed25519 -N "" -f /root/.ssh/ahbo_mac_bridge`
   (only if it doesn't exist). Print **only the public key** for the owner.
2. Write `/opt/ahbo/ios/MAC_BRIDGE.md` (you may create this one file in the project) with the owner's exact Mac steps:
   - install Xcode 26 + command line tools; `brew install node xcodegen`; check `node -v`;
   - System Settings → General → Sharing → **Remote Login** on (for the owner's user only);
   - append the server's bridge public key to `~/.ssh/authorized_keys` on the Mac;
   - sync the project from the server:
     `rsync -a --delete root@srv2135273.hstgr.cloud:/opt/ahbo/{ios,face} ~/ahbo/ && rsync -a root@srv2135273.hstgr.cloud:/opt/ahbo/brain/config ~/ahbo/brain/`
     then `cd ~/ahbo/ios && xcodegen`;
   - open the tunnel while working: `ssh -N -R 127.0.0.1:2222:localhost:22 root@srv2135273.hstgr.cloud`
     (leave that Terminal window open; closing it ends the bridge);
   - test from the Mac side: `npx -y xcodebuildmcp@2.7.0 --help` runs.
3. The Zcode MCP entry to add **after** the owner has done the Mac steps (write it into the report; add it to Zcode
   disabled, or not at all if Zcode can't disable entries):
   ```json
   "xcodebuild": { "command": "ssh",
     "args": ["-i", "/root/.ssh/ahbo_mac_bridge", "-p", "2222", "-o", "StrictHostKeyChecking=accept-new",
              "<MAC_USERNAME>@127.0.0.1", "PATH=/opt/homebrew/bin:/usr/local/bin:$PATH npx -y xcodebuildmcp@2.7.0"] }
   ```
   Check XcodeBuildMCP's README (npm page / GitHub) for its current stdio launch command and any required env vars, and
   correct the entry if it differs.
4. Verify what you can without the Mac: the key exists, the doc is complete, `ssh -p 2222 … true` fails cleanly with
   "connection refused" (tunnel not open yet). When the owner later opens the tunnel, the check is: ask XcodeBuildMCP to
   list schemes for `~/ahbo/ios/AhBo.xcodeproj` and build the `AhBo` scheme for an iOS Simulator.

## Report
Which servers were added and how (Zcode setting or file + the exact entries, versions/tags), what each verification
returned, what was skipped and why, the bridge public key (public part only), and the owner's remaining manual steps.
