# Building the Ah Bo iPad app on a Mac (PRD §25.8, §27.4)

The app was written on the Linux VPS, which can't run Xcode, so **the first real compile happens on your
Mac.** These parts have already been compiled and tested on Linux with Swift 6.2: the wake matcher and its
XCTest (8 of 8 pass), the face state cache, the reminder mirror and the device channel. Every
framework-specific call was checked against the LiveKit 2.18.0 source or Apple's documentation; see
`API_VERIFICATION.md`.

## 1. One-time setup

1. Install **Xcode 26 or later** from the App Store, open it once and accept the licence.
2. Install XcodeGen:
   ```bash
   brew install xcodegen
   ```
3. Get the repo onto the Mac. You need `ios/`, `face/` and `brain/config/`, laid out side by side as in the
   repo, because the project references `../face` and `../brain/config/*.yaml`:
   ```bash
   scp -r root@srv2135273.hstgr.cloud:/opt/ahbo ~/ahbo     # or git clone, if a remote was added
   ```
   **Don't copy `face/` into `ios/`.** The project uses the repo's `face/` as a folder reference.
4. Optional: fetch the fixed clips into the bundle (the offline fallback). On the VPS run
   `make render-fixed`, then copy `ios/AhBo/Resources/fixed/` across. If they're missing, the app downloads
   them from the server on its first connection, and the build still succeeds (the folder is optional).

## 2. Generate and open the project

```bash
cd ~/ahbo/ios && xcodegen && open AhBo.xcodeproj
```

Xcode then resolves the Swift package `client-sdk-swift`, pinned to **exactly 2.18.0**, together with its
WebRTC and FFI binaries. The first resolve downloads about 100 MB.

## 3. Signing (free Apple ID)

In Xcode, open target **AhBo** → **Signing & Capabilities**:
1. Tick **Automatically manage signing**.
2. Set **Team** to your **Personal Team**.
3. If `hk.ahbo.app` is taken, change `PRODUCT_BUNDLE_IDENTIFIER` in `project.yml` (for example to
   `hk.ahbo.app.<yourname>`) and re-run `xcodegen`. Don't edit the generated project by hand: `xcodegen`
   overwrites it.

## 4. Simulator first (§25.8 step 2)

1. Choose an iPad simulator (for example "iPad Air 13-inch") and press **⌘B**. The target is **0 errors**.
2. Run the tests with **⌘U**: `JyutpingMatcherTests` must pass. They read `brain/config/wake_cases.yaml`
   and `ios/AhBo/Resources/wake_homophones.json` straight from the repo.
3. Run with **⌘R**. The face appears and blinks.
4. Open the hidden settings:
   1. Three-finger long press for 3 s. In the Simulator, hold ⌥ for two fingers; three fingers isn't easy
      there, so use a device, or temporarily set `numberOfTouchesRequired` to 2 in `FaceWebView.swift`.
   2. Enter the PIN `2580`.
   3. Fill in **Host**: `srv2135273.hstgr.cloud`.
   4. Fill in **App secret**: on the VPS run `make show-app-secret` and paste the value. It is stored only in
      the Keychain.
   5. Tap **Save and reconnect**.
5. Check the operator console: the device shows online. The S1–S4 buttons drive the face just as
   `sim/ipad.html` does. Tap the face: a session opens (using the Mac's mic).
6. In the Simulator the wake engine reports `none` or `dictation`. That's expected.

## 5. On the iPad Air 5

1. Update to iPadOS 26 or later.
2. Settings → Privacy & Security → **Developer Mode** on, then restart.
3. Connect the cable, select the iPad in Xcode and press **⌘R**.
4. On the iPad, go to Settings → General → **VPN & Device Management** and trust your developer certificate.
5. First launch:
   1. Allow the **microphone**, **speech recognition** and the **camera** (the camera prompt appears at the
      first fall photo; to trigger it early, run a simulated fall from the console).
   2. Open the hidden settings (three fingers, 3 s), enter PIN `2580`, then the host and app secret as above.
   3. **Keep Wi-Fi on while the Cantonese speech model downloads.** The app asks `AssetInventory` for the
      `zh_HK` (or `yue`) model; the log in settings shows "downloading speech model". The settings screen
      should then show the wake engine as `speech_transcriber` (or `dictation`).
   4. **Change the PIN** in settings.
6. Acceptance (§25.8 step 3):
   - 「喂阿寶」 wakes it at 1 m;
   - a simulated fall plays F1 within 1 s;
   - the photo arrives in the console;
   - pulling Wi-Fi shows the amber offline dot after 30 s, and a due reminder still plays.
7. Kiosk:
   - Settings → Accessibility → **Guided Access** on with a passcode; start it in the app with a
     triple-click of the top button;
   - Display → Auto-Lock **Never**;
   - Focus → **Do Not Disturb**;
   - volume high;
   - keep it on the charger.
8. **Free signing expires after 7 days.** Re-run step 5.3 (⌘R with the cable) one or two days before the demo.

## 6. Expected first-build warnings (harmless)

- Swift packages: warnings from inside LiveKit and WebRTC, such as deprecations or "a run script phase will
  be run during every build".
- "Run script build phase 'Strip face/dev.html' will be run during every build". This is intentional: it
  removes `dev.html` from the bundle (PRD §25.5).
- `UIRequiresFullScreen` is deprecated in recent iPadOS SDKs (the PRD asks for it; it's harmless).
- The app icon is a placeholder at a single 1024 px size.
- Swift 6 `Sendable` notes in our code are possible where the SDK's annotations differ from the docs; they
  are warnings, not errors.

## 7. If it doesn't compile: the APIs most likely to have drifted

Everything framework-specific sits in one file per framework, so fix it there:

| Symptom | Where | What to change |
|---|---|---|
| `registerTextStreamHandler` signature or the handler type differs | `Voice/VoiceSession.swift` `open()` | 2.18.0 has `room.registerTextStreamHandler(for:onNewStream:) async throws` with `(TextStreamReader, Participant.Identity) async throws`. On other versions, match the closure parameters; `reader.info.attributes` and iterating `for try await chunk in reader` are unchanged since 2.5. |
| `RoomDelegate` method "nearly matches" a requirement | `Voice/VoiceSession.swift` `RoomEvents` | Use Xcode's fix-it to the exact signatures `room(_:participant:didUpdateAttributes:)` and `room(_:didDisconnectWithError:)`. |
| `AudioManager.shared.audioSession.isAutomaticDeactivationEnabled` missing | `VoiceSession.init` | Delete the line. The app re-applies its own audio session after every room in any case (`restoreAudioSession`). |
| `participant.kind == .agent` fails | `VoiceSession.swift` | Use `participant.isAgent`, or rely only on the `lk.agent.state` attribute check that is already there. |
| `SpeechTranscriber` / `DictationTranscriber` init labels or options differ | `Wake/WakeWordEngine.swift` `makeModule` | Use the presets instead: `SpeechTranscriber(locale:preset: .progressiveTranscription)` and `DictationTranscriber(locale:preset: .progressiveShortDictation)`. |
| `result.range` / `result.text` / `isFinal` differ | `WakeWordEngine.swift` `makeModule` | `range` is used only for the utterance-start check. If it's unavailable, pass `startSec: nil` and the engine falls back to wall-clock energy onsets. |
| `AssetInventory` API differs | `AnalyzerRecognizer.start` | Comment out the download block; install the Cantonese model by hand once (Settings → General → Keyboard → Dictation languages → 粵語 (香港)). |
| `SpeechAnalyzer.start(inputSequence:)` or `bestAvailableAudioFormat` differs | `AnalyzerRecognizer.start` | Same logic: an `AsyncStream<AnalyzerInput>` fed from `AudioTap`. If the whole Speech file fights you, make `AnalyzerRecognizer.choose()` return `nil`. The app then runs touch-only (`wake_engine: none`), which is acceptable for a first build. |
| Swift 6 isolation errors ("main actor-isolated … cannot satisfy nonisolated requirement") | delegate classes | Mark the method `nonisolated` and hop with `Task { @MainActor in … }`, as `RoomEvents` already does. As a last resort, set `SWIFT_VERSION: "5.0"` in `project.yml` and re-run `xcodegen`. |
| `face` folder not found at runtime (black screen, log "face folder missing") | `project.yml` | `../face` must exist next to `ios/`. Re-run `xcodegen`. In the Project navigator, `face` is a **blue** folder. |
| Missing package or "exact version not found" | `project.yml` `packages.LiveKit` | Run File → Packages → Reset Package Caches. The tag `2.18.0` exists (verified 10 Nov 2026). |

Every native feature can be switched off in the hidden settings (wake word, LiveKit, camera), so one failing
part never blocks the rest.
