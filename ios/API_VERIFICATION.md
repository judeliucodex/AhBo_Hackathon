# iOS API verification (PRD §25: "write against verified API documentation only")

The app was written on the Linux VPS, where Xcode can't run, so **it has not been compiled with Xcode or the
iOS SDK.** What was checked, and how:

- **LiveKit Swift SDK:** the source of the pinned tag, `git clone --branch 2.18.0` of
  `https://github.com/livekit/client-sdk-swift` (commit `93a7251057bdb6a80a7b1f6d11464d578ebdc09b`, tagged
  2026-10-16). The same symbols were also checked in 2.19.0 (2026-10-27, the latest tag on 10 Nov 2026).
  2.18.0 is pinned because 2.19.0 moved data streams, which carry our captions, onto a new Rust core.
- **Apple frameworks:** each declaration below was read from Apple's documentation JSON
  (`https://developer.apple.com/tutorials/data/documentation/<path>.json`, the data behind
  `developer.apple.com/documentation/<path>`), fetched on 10 Nov 2026.
- **Pure-Swift parts:** compiled and run on Linux with Swift 6.2 (`swift:6.2-noble` Docker image, Swift 6
  language mode, strict concurrency):
  - `Wake/JyutpingMatcher.swift` plus `AhBoTests/JyutpingMatcherTests.swift`: all 8 tests pass against the
    real `wake_cases.yaml`, `wake_match.yaml` and `wake_homophones.json`. The only change for Linux was
    `Bundle(for:)` → `Bundle.module`.
  - `Face/UIStateCache.swift`, `Media/ReminderMirror.swift` and `Device/DeviceChannel.swift`: compile with
    no warnings. For DeviceChannel, `import UIKit` was swapped for `FoundationNetworking`.
  - `Media/FixedClipPlayer.swift` and `Media/VoiceNotePlayer.swift`: compile against a stand-in
    `AVAudioPlayer`, which checks our own Swift-6 concurrency but **not** the real AVFoundation surface.

**Isolation:** each framework is imported only where it's used:
- LiveKit: only `Voice/VoiceSession.swift`;
- Speech: only `Wake/WakeWordEngine.swift` (behind the `WakeRecognizer` protocol) and one authorization
  call in `App/AppModel.swift`;
- WebKit: only `Face/FaceWebView.swift`;
- AVCapture: only `Media/PhotoCapture.swift`.

A drifted API is therefore a compile error in one file.

Legend: **Y** = the declaration was read in the source or docs and matches our call. **Y\*** = the
declaration matches, but the runtime behaviour must be checked on the device. **N** = not verified (see
the notes).

## LiveKit Swift SDK 2.18.0 (`Voice/VoiceSession.swift`)

| Symbol as used | Source file in the tag | Verified |
|---|---|---|
| `Room()` (`public convenience init()`), `room.add(delegate:)` | `Sources/LiveKit/Core/Room.swift`, `Core/Room+MulticastDelegate.swift` | Y |
| `room.connect(url:token:)` (`connectOptions:`/`roomOptions:` default nil) | `Core/Room.swift:379` | Y |
| `room.disconnect() async` | `Core/Room.swift:524` | Y |
| `room.registerTextStreamHandler(for:onNewStream:) async throws` | `Core/Room+DataStream.swift:48` (`public extension Room`) | Y |
| `room.unregisterTextStreamHandler(for:) async` | `Core/Room+DataStream.swift:71` | Y |
| `TextStreamHandler = @Sendable (TextStreamReader, Participant.Identity) async throws -> Void` | `DataStream/Incoming/IncomingStreamManager.swift:361` | Y |
| `TextStreamReader: AsyncSequence` (Element `String`), `reader.info.id`, `.info.attributes: [String:String]` | `DataStream/Incoming/TextStreamReader.swift`, `DataStream/StreamInfo.swift` | Y |
| `Participant.Identity.stringValue`, `Track.Sid.stringValue` | `Types/Participant+Types.swift`, `Types/Track+Types.swift` | Y |
| `room.localParticipant.setMicrophone(enabled:) async throws -> LocalTrackPublication?` | `Participant/LocalParticipant.swift:455` | Y |
| `TrackPublication.sid: Track.Sid` | `TrackPublications/TrackPublication.swift:27` | Y |
| `room.remoteParticipants: [Participant.Identity: RemoteParticipant]` | `Core/Room.swift` | Y |
| `Participant.attributes`, `.kind` (`Kind.agent`), `.audioLevel: Float`, `.identity` | `Participant/Participant.swift`, `Participant/Participant+Kind.swift` | Y |
| `RoomDelegate.room(_:participant:didUpdateAttributes:)` | `Protocols/RoomDelegate.swift:129` | Y |
| `RoomDelegate.room(_:didDisconnectWithError:)` | `Protocols/RoomDelegate.swift:78` | Y |
| `AudioManager.shared.audioSession.isAutomaticDeactivationEnabled` (settable) | `Audio/Manager/AudioManager.swift:109,550`, `Audio/AudioSessionEngineObserver.swift:49` | Y |
| Attribute keys `lk.agent.state`, `lk.segment_id`, `lk.transcribed_track_id`, `lk.transcription_final`; `AgentState` raw values `idle initializing listening speaking thinking` | `Types/Attributes/AttributeTypings.swift:63,79,99-101` | Y |
| Topic `lk.transcription` | `Agent/Chat/Receive/TranscriptionStreamReceiver.swift:83`. The SDK registers it itself only when you use its `Session` API, which we don't, so our registration doesn't collide. | Y |
| Agent transcriptions are paced to audio playout (`sync_transcription`) | server side (livekit-agents 1.8.11; owned by the agent worker) | N (not ours to verify) |
| `Participant.audioLevel` updates often enough for a 20 Hz mouth | runtime behaviour | N: if it's coarse, the face falls back to its random flap after 300 ms, which is acceptable |

## Speech (iPadOS 26) (`Wake/WakeWordEngine.swift`)

| Symbol | Doc path (under developer.apple.com/documentation/) | Introduced | Verified |
|---|---|---|---|
| `final actor SpeechAnalyzer`; `init(modules:options:)` (options default nil) | speech/speechanalyzer, …/init(modules:options:) | iOS/iPadOS 26.0 | Y |
| `SpeechAnalyzer.start(inputSequence:) async throws` (Element == `AnalyzerInput`) | speech/speechanalyzer/start(inputsequence:) | 26.0 | Y |
| `SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith:) async -> AVAudioFormat?` | speech/speechanalyzer/bestavailableaudioformat(compatiblewith:) | 26.0 | Y |
| `SpeechAnalyzer.cancelAndFinishNow() async` | speech/speechanalyzer/cancelandfinishnow() | 26.0 | Y |
| `AnalyzerInput(buffer: AVAudioPCMBuffer)` | speech/analyzerinput/init(buffer:)-2ysg3 | 26.0 | Y |
| `SpeechTranscriber(locale:transcriptionOptions:reportingOptions:attributeOptions:)` | speech/speechtranscriber/init(locale:transcriptionoptions:reportingoptions:attributeoptions:) | 26.0 | Y |
| `SpeechTranscriber.ReportingOption.volatileResults`, `.fastResults`; `ResultAttributeOption.audioTimeRange` | speech/speechtranscriber/reportingoption, …/preset | 26.0 | Y |
| `SpeechTranscriber.isAvailable: Bool { get }` | speech/speechtranscriber/isavailable | 26.0 | Y |
| `SpeechTranscriber.supportedLocales: [Locale] { get async }` | speech/speechtranscriber/supportedlocales | 26.0 | Y |
| `SpeechTranscriber.results` (AsyncSequence of `Result`); `Result.text: AttributedString`, `.isFinal`, `.range: CMTimeRange` | speech/speechtranscriber/result, speech/speechmoduleresult | 26.0 | Y |
| `DictationTranscriber(locale:contentHints:transcriptionOptions:reportingOptions:attributeOptions:)`; `ContentHint.shortForm`, `.farField`; `ReportingOption.volatileResults`, `.frequentFinalization`; `ResultAttributeOption.audioTimeRange` | speech/dictationtranscriber/… | 26.0 | Y |
| `DictationTranscriber.supportedLocales { get async }`, `.results` | speech/dictationtranscriber/supportedlocales | 26.0 | Y |
| `AssetInventory.assetInstallationRequest(supporting:) async throws -> AssetInstallationRequest?`; `AssetInstallationRequest.downloadAndInstall() async throws` | speech/assetinventory/assetinstallationrequest(supporting:), speech/assetinstallationrequest | 26.0 | Y |
| `AssetInventory.reserve(locale:) async throws -> Bool` (`@discardableResult`) | speech/assetinventory/reserve(locale:) | 26.0 | Y |
| `SFSpeechRecognizer.requestAuthorization(_:)`: the handler is **not** `@Sendable`, so we call it from a `nonisolated` helper | speech/sfspeechrecognizer/requestauthorization(_:) | 10.0 | Y |
| `zh_HK` or `yue` is in `supportedLocales`, and the model installs on the iPad Air 5 | runtime (PRD §20.4.0 day-1 check) | — | **N: check on the device** (the settings screen shows the engine; the log shows the locale) |
| Whether SpeechAnalyzer needs speech-recognition authorization | runtime | — | N: we request it anyway, which is harmless |

## AVFoundation / AVFAudio

| Symbol | Doc path | Verified |
|---|---|---|
| `AVAudioSession.setCategory(_:mode:options:)`, `.playAndRecord`, `.voiceChat`, options `.defaultToSpeaker`, `.allowBluetoothA2DP` | avfaudio/avaudiosession/setcategory(_:mode:options:), …/categoryoptions-swift.struct/allowbluetootha2dp | Y |
| `AVAudioSession.setActive(_:options:)` | avfaudio/avaudiosession/setactive(_:options:) | Y |
| `AVAudioSession.routeChangeNotification`, `.interruptionNotification` | avfaudio/avaudiosession/routechangenotification, …/interruptionnotification | Y |
| `AVAudioSession.mediaServicesWereResetNotification` | (long-standing API; page not fetched) | N (low risk) |
| `AVAudioApplication.requestRecordPermission()` async form (generated from `requestRecordPermission(completionHandler:)`, iOS 17) | avfaudio/avaudioapplication/requestrecordpermission(completionhandler:) | Y |
| `AVAudioPlayer(contentsOf:)`, `.isMeteringEnabled`, `.updateMeters()`, `.averagePower(forChannel:)`, `.play() -> Bool`, `.duration`, `.isPlaying`, `.stop()`, `.prepareToPlay()` | avfaudio/avaudioplayer/… | Y |
| `AVAudioEngine.inputNode`, `AVAudioNode.installTap(onBus:bufferSize:format:block:)`, `outputFormat(forBus:)`, `removeTap(onBus:)` | avfaudio/avaudioengine/inputnode, avfaudio/avaudionode/… | Y |
| `AVAudioInputNode.setVoiceProcessingEnabled(_:) throws` | avfaudio/avaudioionode/setvoiceprocessingenabled(_:) | Y |
| `AVAudioConverter(from:to:)`, `convert(to:error:withInputFrom:)` (the input block is non-escaping) | avfaudio/avaudioconverter/… | Y |
| `AVAudioPCMBuffer(pcmFormat:frameCapacity:)`, `.floatChannelData` | (page path 404; long-standing API) | N (low risk) |
| `AVCaptureDevice.default(_:for:position:)`, `.builtInWideAngleCamera`, `.front` | avfoundation/avcapturedevice/default(_:for:position:) | Y |
| `AVCaptureDevice.requestAccess(for:)` async form | avfoundation/avcapturedevice/requestaccess(for:completionhandler:) | Y |
| `AVCapturePhotoSettings(format:)`, `AVCapturePhotoOutput.capturePhoto(with:delegate:)`, delegate `photoOutput(_:didFinishProcessingPhoto:error:)`, `AVCapturePhoto.fileDataRepresentation()` | avfoundation/… | Y |
| `AVCaptureSession.startRunning()` (blocking; called off the main thread) | avfoundation/avcapturesession/startrunning() | Y |

## WebKit, UIKit, SwiftUI, Foundation, Security

| Symbol | Doc path | Verified |
|---|---|---|
| `WKWebViewConfiguration.mediaTypesRequiringUserActionForPlayback = []`, `allowsInlineMediaPlayback` | webkit/wkwebviewconfiguration/mediatypesrequiringuseractionforplayback | Y\*: whether the face's Web Audio plays without a tap needs checking on the device (PRD) |
| `WKUserContentController.add(_:name:)`, `WKScriptMessageHandler.userContentController(_:didReceive:)` | webkit/wkusercontentcontroller/add(_:name:) | Y |
| `WKWebView.evaluateJavaScript(_:completionHandler:)` (`@MainActor @Sendable` handler) | webkit/wkwebview/evaluatejavascript(_:completionhandler:) | Y |
| `WKWebView.loadFileURL(_:allowingReadAccessTo:)`, `isInspectable` (16.4+) | webkit/wkwebview/… | Y |
| `WKNavigationDelegate.webViewWebContentProcessDidTerminate(_:)` | webkit/wknavigationdelegate/webviewwebcontentprocessdidterminate(_:) | Y |
| `UILongPressGestureRecognizer.numberOfTouchesRequired`, `minimumPressDuration` | uikit/uilongpressgesturerecognizer/numberoftouchesrequired | Y |
| `UIApplication.isIdleTimerDisabled`, `UIDevice.batteryLevel`, `ProcessInfo.thermalState` | uikit/…, foundation/processinfo/thermalstate-swift.property | Y |
| `UIGraphicsImageRenderer`, `UIImage.jpegData(compressionQuality:)` | uikit/… | Y |
| SwiftUI `.persistentSystemOverlays(.hidden)` (hides the home indicator), `.statusBarHidden(_:)`, `.preferredColorScheme(_:)` | swiftui/view/… | Y. `prefersHomeIndicatorAutoHidden` is a UIKit view-controller property; with the SwiftUI life cycle, `persistentSystemOverlays(.hidden)` is its equivalent |
| `@Observable` (Observation) | observation/observable() | Y. Whether `didSet` on `@Observable` stored properties works in `AppSettings` is runtime/compiler behaviour: Y\* |
| `URLSessionWebSocketTask.send(_:) / send(_:completionHandler:)` (`@Sendable`), `receive() async throws`, `sendPing(pongReceiveHandler:)` (`@Sendable`), `cancel(with:reason:)`, `maximumMessageSize`, `closeCode` | foundation/urlsessionwebsockettask | Y (also compiled on Linux) |
| `URLSession.data(for:)`, `upload(for:from:)` async | foundation/urlsession/… | Y |
| `NotificationCenter.addObserver(forName:object:queue:using:)` (`@Sendable` block) | foundation/notificationcenter/addobserver(forname:object:queue:using:) | Y |
| `SecItemAdd`, `SecItemCopyMatching`, `SecItemDelete`, `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly` | security/secitemadd(_:_:) | Y |

## Tooling

| Item | Value | Verified |
|---|---|---|
| XcodeGen | 2.46.0 (latest tag); spec keys `packages.<n>.exactVersion`, sources `type: folder`, `optional`, `buildPhase`, `excludes`, `postBuildScripts` (`basedOnDependencyAnalysis`), `options.developmentLanguage` | Y (`Docs/ProjectSpec.md` on master) |
| `excludes` on a `type: folder` source | ProjectSpec doesn't say whether excludes apply to folder references | **N**: the build script "Strip face/dev.html" removes `dev.html` from the bundle regardless |
| LiveKit 2.18.0 `Package.swift` | `swift-tools-version:6.1` (needs Xcode 16.3 or later; Xcode 26 is fine) | Y |
