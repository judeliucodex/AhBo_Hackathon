// FaceWebView.swift — hosts the face kit (../face, bundled as a folder reference) in a WKWebView (PRD §25.5).
//
// FaceController owns the web view (a UIViewRepresentable is a value type, so the state lives here):
//  * apply(cmd): JSONSerialization → evaluateJavaScript("window.AhBo.apply(<json>)")
//  * commands are queued until the face reports `ready`, then flushed in order
//  * on `ready` after a reload (or a web-content-process crash) the UIStateCache is replayed first
//  * the script-message handler is a weak proxy (the classic WKUserContentController retain cycle)
//  * a three-finger long press (3 s) on the web view opens the hidden settings (§25.7)

import SwiftUI
import UIKit
import WebKit

/// Weak proxy so WKUserContentController doesn't retain the controller (and through it the web view).
final class WeakScriptMessageProxy: NSObject, WKScriptMessageHandler {
    weak var target: (any WKScriptMessageHandler)?
    init(_ target: any WKScriptMessageHandler) { self.target = target }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        target?.userContentController(userContentController, didReceive: message)
    }
}

@MainActor
final class FaceController: NSObject, WKScriptMessageHandler, WKNavigationDelegate, UIGestureRecognizerDelegate {
    let webView: WKWebView
    let cache = UIStateCache()
    /// Face events (ready, tap, ok_tapped, countdown_done, say_done, birth_done, error), as dictionaries.
    var onEvent: (([String: Any]) -> Void)?
    var onSettingsGesture: (() -> Void)?

    private(set) var isReady = false
    private var everReady = false
    private var queue: [[String: Any]] = []
    private static let maxQueue = 300

    override init() {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []     // face Web Audio pops/beeps (VERIFY on device)
        webView = WKWebView(frame: .zero, configuration: config)
        super.init()
        config.userContentController.add(WeakScriptMessageProxy(self), name: "ahbo")
        webView.navigationDelegate = self
        webView.isOpaque = false
        webView.backgroundColor = .black
        webView.scrollView.backgroundColor = .black
        webView.scrollView.isScrollEnabled = false
        webView.scrollView.bounces = false
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        #if DEBUG
        webView.isInspectable = true                              // Safari Web Inspector on the Mac
        #endif
        let press = UILongPressGestureRecognizer(target: self, action: #selector(settingsPress(_:)))
        press.numberOfTouchesRequired = 3
        press.minimumPressDuration = 3.0
        press.cancelsTouchesInView = false
        press.delegate = self
        webView.addGestureRecognizer(press)
        load()
    }

    func load() {
        guard let faceDir = Bundle.main.url(forResource: "face", withExtension: nil) else {
            Log.error("face", "face folder missing from the bundle")
            return
        }
        isReady = false
        webView.loadFileURL(faceDir.appendingPathComponent("index.html"), allowingReadAccessTo: faceDir)
    }

    /// Send one face command (§23.4). Persistent state goes into the cache for replay.
    func apply(_ cmd: [String: Any]) {
        cache.update(cmd)
        send(cmd, queueIfNotReady: (cmd["type"] as? String) != "level")
    }

    /// Send without touching the cache (local, provisional looks such as the wake `listening` mood).
    func applyTransient(_ cmd: [String: Any]) {
        send(cmd, queueIfNotReady: false)
    }

    private func send(_ cmd: [String: Any], queueIfNotReady: Bool) {
        guard isReady else {
            if queueIfNotReady {
                queue.append(cmd)
                if queue.count > Self.maxQueue { queue.removeFirst(queue.count - Self.maxQueue) }
            }
            return
        }
        evaluate(cmd)
    }

    private func evaluate(_ cmd: [String: Any]) {
        guard JSONSerialization.isValidJSONObject(cmd),
              let data = try? JSONSerialization.data(withJSONObject: cmd),
              let json = String(data: data, encoding: .utf8) else {
            Log.error("face", "command not serialisable: \(cmd["type"] as? String ?? "?")")
            return
        }
        webView.evaluateJavaScript("window.AhBo.apply(\(json))") { _, error in
            if let error { Log.error("face", "apply failed: \(error.localizedDescription)") }
        }
    }

    // MARK: WKScriptMessageHandler

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "ahbo", let evt = message.body as? [String: Any],
              let type = evt["type"] as? String else { return }
        if type == "ready" {
            let replay = everReady
            isReady = true
            everReady = true
            if replay {
                Log.info("face", "ready after reload: replaying state")
                cache.replayCommands().forEach(evaluate)
            }
            let pending = queue
            queue.removeAll()
            pending.forEach(evaluate)
        }
        onEvent?(evt)
    }

    // MARK: WKNavigationDelegate

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        Log.error("face", "web content process terminated: reloading")
        isReady = false
        webView.reload()
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: any Error) {
        retryLoad(error)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: any Error) {
        retryLoad(error)
    }

    private func retryLoad(_ error: any Error) {
        Log.error("face", "load failed: \(error.localizedDescription); retrying in 2 s")
        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(2))
            self?.load()
        }
    }

    // MARK: hidden settings gesture

    @objc private func settingsPress(_ g: UILongPressGestureRecognizer) {
        if g.state == .began { onSettingsGesture?() }
    }

    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                           shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool { true }
}

/// SwiftUI wrapper around the controller's web view.
struct FaceWebView: UIViewRepresentable {
    let controller: FaceController

    func makeUIView(context: Context) -> WKWebView { controller.webView }
    func updateUIView(_ uiView: WKWebView, context: Context) {}
}
