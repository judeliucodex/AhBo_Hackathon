# Ah Bo face kit: copy this, do not redesign it

This folder is the **production face** of Ah Bo, the screen the iPad shows. It is the approved demo face (`../original-demo-face.html`) turned into a renderer driven by commands, with the extra parts the MVP needs:

- moods;
- check-mark progress steps;
- "what she said" captions;
- cards;
- the 「我冇事」 button;
- the countdown.

**Builder rule:** copy these five files into the repo's `face/` folder unchanged. Then extend them only by adding to them. Do not change any colour, size, font, timing or easing. The PRD's interface chapter (§23) refers to this kit by file name.

| File | What it is |
|---|---|
| `index.html` | The page the iPad's WKWebView loads. Markup only. |
| `face.css` | All styling. The tokens in `:root` come from the demo. |
| `face.js` | The renderer. It exposes `window.AhBo.apply(cmd)` and reports events through the bridge. |
| `bridge.js` | Host detection. In native mode it uses `webkit.messageHandlers.ahbo`; in an iframe it uses `parent.postMessage`; in a plain browser it logs to the console. |
| `dev.html` | Dev harness: an iPad-sized frame, every command as a button, the S1–S4 storyboards, and an event log. Not shipped to the iPad. |

## Run it

```bash
cd ui-reference/face && python3 -m http.server 8766
```

Then open `http://localhost:8766/dev.html` and click **S1–S4**. On the VPS, once copied to `face/`, the same page is served at `/face/dev.html`.

- The page works offline except for the rounded web font, which falls back to PingFang HK on iPad.
- Use `?kiosk=1` to hide the cursor. The cursor is hidden automatically inside the iPad app.

## What it looks like (same visual language as the demo)

- **Stage:** pure black, with two round `#ECE3E7` eyes, each 22 vmin, 7.5 vmin apart. Below them: the mouth bar, a dim "heard" line (her words), the caption, then the cards.
- **Idle life:**
  - blinks every 2.2–5.4 s, with a 25% chance of a double blink;
  - glances ±5/±2.5 vmin every 1.6–4 s;
  - never moves while speaking.
- **Captions:** one span per character. Spoken characters go from 18% to 100% opacity in step with the audio. Long lines (over 36 characters) show one sentence at a time. The caption fades 2 s after the line ends, then the eyes do a happy squint for 1.4 s.
- **Cards:** rounded pills with an icon (or a two-colour medicine capsule) plus a label and a dim small line. A card with `when` waits until that phrase is spoken.
- **Steps:**
  - pending: hollow ring;
  - active: pulsing ring with a bright label;
  - done: filled disc with a drawn ✓;
  - failed: amber disc with ✕.
  - Placement: in landscape they sit on the right and the face slides left; in portrait they sit at the bottom.
- **Moods:**

  | Mood | Look |
  |---|---|
  | `idle` | Default; glances around |
  | `listening` | Slightly wider eyes and a slow breathing ring |
  | `thinking` | Eyes look up-left; three dots replace the mouth |
  | `curious` | Head tilt |
  | `warm` | Pink glow and a soft smile (family, voice notes) |
  | `alarm` | Amber pulsing eyes plus a two-tone beep (falls) |
  | `sleepy` | Droopy, dim, slow blinks (quiet hours) |

- **Expressions:** `blink`, `happy` (the squint), `alert` (a quick widen), `wake` (jump, widen, smile).
- **Birth:** black screen, then the left eye pops in, then the right, each with a pop tone, a ripple ring and 10 sparks. Same as the demo.

## Contract

### Commands (host → face)

Send with `webView.evaluateJavaScript("window.AhBo.apply(" + json + ")")`, or `postMessage({ahbo: cmd})` from a parent frame.

| type | fields | notes |
|---|---|---|
| `mood` | `mood` | One of the seven moods above. `alarm` also beeps. |
| `expr` | `expr` | `blink` \| `happy` \| `alert` \| `wake` |
| `heard` | `text`, `final?` | Her words, partial or final. `""` clears the line. Final text fades after 6 s. |
| `say_start` | `id`, `text?`, `from?`, `fromName?`, `segments?`, `durationSec?`, `paged?` | Starts a caption.<br>`from:"family"` plays a voice note: no mouth, and the caption is prefixed with 「fromName：」.<br>`segments` = `[[firstChar, endChar, startSec, endSec], …]` from Azure word boundaries for fixed clips. The clock starts when the command arrives, so send it at the moment playback starts.<br>With neither `segments` nor `durationSec`, the host drives the reveal with `say_update`. |
| `say_update` | `id`, `text?`, `revealed?` | Streaming captions (LiveKit transcription). `text` may grow. `revealed` = how many characters have been spoken so far, counted in **UTF-16 code units** (Swift: `String(prefix).utf16.count`). Updates for a different `id` are ignored, which covers barge-in. |
| `say_end` | `id` | Reveals everything, shows all waiting cards, stops the mouth, fades the caption, emits `say_done`. |
| `level` | `v` (0–1) | Output audio level at about 20 Hz while Ah Bo talks. The mouth follows it; if no level arrives for 300 ms, the mouth uses the demo's random flap. |
| `card` | `card:{id, kind, label, small?, icon?, when?, persistent?, pill?, wave?, photo?}` | Same `id` = replace.<br>`kind` ∈ `med`, `event`, `plan`, `voice_note`, `incident`, `action`, `message`, `story`, `today`, `photo`, `activity`, `info`.<br>`pill` = two CSS colours or token names, for example `["--pill-b","--pill-a"]`.<br>`photo` = image URL or data URL. |
| `card_update` | `id`, `patch:{label?, small?, icon?, playing?}` | `playing` animates the voice-note waveform. |
| `card_clear` | `id?` | With no id, clears every card that isn't `persistent`. |
| `steps` | `group:{id, title?, steps:[{id, label, doneLabel?, state?}]}` | Creates or replaces a group. |
| `step` | `group`, `id`, `state`, `label?` | `state` ∈ `pending` \| `active` \| `done` \| `failed`. When a step turns `done`, `doneLabel` replaces its label. |
| `steps_clear` | `group?` | |
| `ok_button` | `show`, `label?` | The big 「我冇事」 button. |
| `countdown` | `id`, `n` | An amber chip that counts n…1 with a beep each second. Emits `countdown_done`. It stops silently if it is cleared with `card_clear`. |
| `flash` | | Camera flash plus a shutter sound. |
| `birth` | | The demo birth scene. Emits `birth_done`. |
| `offline` | `on` | A tiny amber dot, top right. |
| `reset` | | Back to an idle, empty face. |

### Events (face → host)

| type | fields | when |
|---|---|---|
| `ready` | `version` | The page has loaded. The host must wait for it before sending commands, and must replay current state after a reload. |
| `tap` | `target:"face"\|"card"`, `id?` | Touch-to-talk (face) or a card tap. |
| `ok_tapped` | | The 「我冇事」 button was pressed. |
| `countdown_done` | `id` | The countdown reached 0. |
| `say_done` | `id` | After `say_end`. |
| `birth_done` | | |
| `error` | `message` | A bad command. Log it; the face itself never throws. |

### Rules the host must follow

1. **The face shows; it never decides.** Safety logic, such as counting down to escalation, runs in the Care Kernel on the server. `countdown_done` and `ok_tapped` are reported to the server as signals only.
2. **Keep `say_*` ids unique per line.** On barge-in, the host sends `say_end` for the cut line straight away.
3. **Send each step's state as it happens.** Never batch a step's `active` and `done` into one frame: the user should see the ✓ draw.
4. **Text is Traditional Chinese (Hong Kong Cantonese).** Never Simplified. Never Mandarin.
