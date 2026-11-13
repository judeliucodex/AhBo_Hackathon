# Mac build bridge for the Ah Bo iOS app (XcodeBuildMCP over SSH)

ZCode (on the server `srv2135273.hstgr.cloud`) builds and tests the iPad app by reaching
**XcodeBuildMCP on the owner's Mac** over SSH. Xcode + macOS are only available on the Mac, so the
Mac is a **build mirror**:

- **The server (`/opt/ahbo`) stays the single source of truth.** All edits happen on the server;
  the Mac copy is overwritten by rsync and never edited by hand.
- The Mac is usually behind home NAT, so the **Mac opens a reverse SSH tunnel to the server**.
  The tunnel binds to `127.0.0.1:2222` on the server only — no VPN, no firewall/router change,
  nothing exposed publicly. Zcode connects to `127.0.0.1:2222`, which lands on the Mac's sshd.

---

## One-time setup on the Mac

### 1. Install the toolchain
- Install **Xcode 26** from the Mac App Store, then the command line tools:
  ```bash
  xcode-select --install
  ```
- Install Node and XcodeGen via Homebrew, and check versions:
  ```bash
  brew install node xcodegen
  node -v        # must print v18 or later
  ```

### 2. Enable Remote Login (SSH) — for your user only
System Settings → General → Sharing → turn on **Remote Login**
("Allow full disk access" is not needed; restrict "Allow access for" to your own user).

### 3. Authorise the server's bridge key
Append this one-line public key to `~/.ssh/authorized_keys` on the Mac
(create the file with `mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys` if needed):

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBVbnUakGGbdrz3MpMG+3nktCODw//uoGQGzGWMW4XYW ahbo-mac-bridge (ZCode XcodeBuildMCP)
```

This is the **public** half of `/root/.ssh/ahbo_mac_bridge` on the server; the private half never
leaves the server and is used only by Zcode's `xcodebuild` MCP entry.

### 4. Mirror the project from the server and generate the Xcode project
```bash
mkdir -p ~/ahbo
rsync -a --delete root@srv2135273.hstgr.cloud:/opt/ahbo/{ios,face} ~/ahbo/ && rsync -a root@srv2135273.hstgr.cloud:/opt/ahbo/brain/config ~/ahbo/brain/
cd ~/ahbo/ios && xcodegen
```
`face/` and `brain/config/` are copied read-only for the build; never edit them on the Mac
(all edits happen on the server, then re-run the rsync above to re-sync).

### 5. Test XcodeBuildMCP from the Mac side
```bash
npx -y xcodebuildmcp@2.7.0 --help
```
This should print the XcodeBuildMCP help (it downloads on demand, no global install).
The MCP server itself is started with the `mcp` subcommand — that is what Zcode runs remotely:
`npx -y xcodebuildmcp@2.7.0 mcp` (stdio transport; no required environment variables).

---

## Every working session (while building the iOS app)

Open the reverse tunnel on the Mac and **leave that Terminal window open** (closing it ends the bridge):
```bash
ssh -N -R 127.0.0.1:2222:localhost:22 root@srv2135273.hstgr.cloud
```

Then tell Zcode on the server to use the `xcodebuild` MCP server. Sanity check from the server:
```bash
ssh -i /root/.ssh/ahbo_mac_bridge -p 2222 -o StrictHostKeyChecking=accept-new judeliu@127.0.0.1 true && echo BRIDGE-OK
```

The check that the whole chain works: ask Zcode to **list the schemes of `~/ahbo/ios/AhBo.xcodeproj`**
(Mac side: `/Users/judeliu/ahbo/ios/AhBo.xcodeproj`) and to **build the `AhBo` scheme for an iOS Simulator**.

---

## Zcode configuration (server side)

`/root/.zcode/cli/config.json` already contains the `xcodebuild` entry, added **disabled**. After the
Mac steps above are done, replace `<MAC_USERNAME>` with your macOS username and set `"enabled": true`
(Settings → MCP in Zcode, or edit the file and restart the session):

```json
"xcodebuild": {
  "type": "stdio",
  "command": "ssh",
  "args": ["-i", "/root/.ssh/ahbo_mac_bridge", "-p", "2222", "-o", "StrictHostKeyChecking=accept-new",
           "-o", "BatchMode=yes",
           "judeliu@127.0.0.1", "PATH=/opt/homebrew/bin:/usr/local/bin:$PATH npx -y xcodebuildmcp@2.7.0 mcp"],
  "enabled": true
}
```

Notes:
- `PATH=/opt/homebrew/bin:/usr/local/bin:$PATH` makes `npx` (Homebrew Node) reachable from the
  non-interactive SSH session. On this Mac `/usr/local/bin` also holds an older node/npm/npx that
  shadows Homebrew's in interactive shells — the entry's PATH puts `/opt/homebrew/bin` first, so
  Homebrew's Node (v24.18.1) is the one used.
- The tunnel must be open (previous section) or the connection fails with "connection refused" —
  that is the expected state when the Mac is offline.

## Status on this Mac (checked 11 Nov 2026)
- macOS username: **judeliu** (machine `Judes-MacBook-Pro-33138`, Apple Silicon — Homebrew at
  `/opt/homebrew`, macOS 26.6.1).
- Bridge key installed in `~/.ssh/authorized_keys` (1 entry) ✅
- Remote Login (sshd) ON ✅ — reverse tunnel verified working (`ssh -p 2222 judeliu@127.0.0.1` OK).
- Homebrew Node v26.13.0 + xcodegen ✅; project synced and generated:
  `/Users/judeliu/ahbo/ios/AhBo.xcodeproj` ✅
- XcodeBuildMCP over the bridge: `initialize` + 24 tools listed ✅ (`list_schemes`, `build_sim`,
  `build_run_sim`, `list_sims`, `screenshot`, …).
- **Xcode selected and build verified** ✅ — Xcode 27.0 (27A266a), iOS runtimes 26.5 + 27.0.
  (Was initially unselected: `xcode-select -p` pointed at CommandLineTools; fixed with
  `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer` + `sudo xcodebuild -runFirstLaunch`.)
- **End-to-end build verified from the server** ✅ — over the bridge via Zcode:
  `list_schemes` → `AhBo`; `build_sim` on **iPad Pro 13-inch (M5), iOS 27.0
  (UDID B3C5E078-F1F3-4F24-BBA5-12B0F8DB1D9D)** → "Build succeeded" in 33s.
- **AhBo is iPad-only**: xcodebuild destinations for iPhone sims are incompatible
  ("doesn't match any of AhBo.app's targeted device families") — always build/run on an iPad sim.
- **After every rsync, re-run `xcodegen`** (step 4): `rsync --delete` removes the generated
  `AhBo.xcodeproj` because it exists only on the Mac.
