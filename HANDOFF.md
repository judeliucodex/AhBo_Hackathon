# Ah Bo MVP: hand-off folder

This folder is everything the building agent needs. Nothing outside it is required.

| File | What it is |
|---|---|
| `GOD_PROMPT.md` | **Paste this as the first message** to the builder (Claude Opus 5.5, high effort). It starts with `ultracode` and tells the builder to use multi-agent workflows. |
| `PRD_ahbo.md` | The complete specification (27 chapters): behaviour, safety, contracts, interface, iPad app, config, seed data, and the owner's setup list (§27, last). |
| `ui-reference/face/` | The face as working code (eyes, captions, check-mark steps, cards, moods, the 「我冇事」 button). The builder copies it as is. Open `dev.html` through any static server to see every scenario. |
| `ui-reference/original-demo-face.html` | The approved demo face it came from (audio removed). Visual reference only. |

## How to start the build

1. Put the **contents** of this folder into the empty project folder **on the VPS**, where the builder will run. For example, from the Mac:

   ```bash
   scp -r ~/Documents/Hack/ahbo-mvp-handoff/. root@srv2135273.hstgr.cloud:/opt/ahbo/
   ```

2. Start Claude Code in that folder on the VPS, with **Opus 5.5** at **high** effort.
3. Paste the whole of `GOD_PROMPT.md` as the first message.
4. The builder works in fake mode, so no keys are needed. When it finishes, log in to `https://srv2135273.hstgr.cloud`, then follow the generated `SETUP.md` to add keys, build the iPad app on your Mac, and set up the iPad.
