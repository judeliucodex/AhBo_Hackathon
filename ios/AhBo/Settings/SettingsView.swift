// SettingsView.swift — the hidden settings (PRD §25.2, §25.7). Reached only by a three-finger long press for
// 3 s on the face plus a 4-digit PIN (default 2580, changeable). The app secret lives in the Keychain; the
// rest in UserDefaults. Owner-facing only: Po-po never sees this screen.

import Security
import SwiftUI

// MARK: - Keychain

enum Keychain {
    private static let service = "hk.ahbo.app"

    static func get(_ account: String) -> String? {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                kSecAttrService as String: service,
                                kSecAttrAccount as String: account,
                                kSecReturnData as String: true,
                                kSecMatchLimit as String: kSecMatchLimitOne]
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    @discardableResult
    static func set(_ account: String, _ value: String) -> Bool {
        let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                   kSecAttrService as String: service,
                                   kSecAttrAccount as String: account]
        SecItemDelete(base as CFDictionary)
        guard !value.isEmpty else { return true }
        var add = base
        add[kSecValueData as String] = Data(value.utf8)
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(add as CFDictionary, nil) == errSecSuccess
    }
}

// MARK: - Stored settings

@MainActor
@Observable
final class AppSettings {
    private let d = UserDefaults.standard

    var host: String { didSet { d.set(host, forKey: "host") } }
    var wakeOn: Bool { didSet { d.set(wakeOn, forKey: "wakeOn") } }
    var liveKitOn: Bool { didSet { d.set(liveKitOn, forKey: "liveKitOn") } }
    var cameraOn: Bool { didSet { d.set(cameraOn, forKey: "cameraOn") } }
    var interruptionOn: Bool { didSet { d.set(interruptionOn, forKey: "interruptionOn") } }
    var debugOverlay: Bool { didSet { d.set(debugOverlay, forKey: "debugOverlay") } }
    let deviceId: String

    init() {
        d.register(defaults: ["host": "", "wakeOn": true, "liveKitOn": true, "cameraOn": true,
                              "interruptionOn": true, "debugOverlay": false])
        host = d.string(forKey: "host") ?? ""
        wakeOn = d.bool(forKey: "wakeOn")
        liveKitOn = d.bool(forKey: "liveKitOn")
        cameraOn = d.bool(forKey: "cameraOn")
        interruptionOn = d.bool(forKey: "interruptionOn")
        debugOverlay = d.bool(forKey: "debugOverlay")
        #if DEBUG
        // Simulator only: the hidden settings need a three-finger press the Simulator can't do. The launch
        // environment (`SIMCTL_CHILD_AHBO_HOST` / `SIMCTL_CHILD_AHBO_APP_SECRET`) seeds them; never in Release.
        let env = ProcessInfo.processInfo.environment
        if let h = env["AHBO_HOST"], !h.isEmpty { host = h; d.set(h, forKey: "host") }
        if let s = env["AHBO_APP_SECRET"], !s.isEmpty { Keychain.set("app_secret", s) }
        #endif
        if let id = d.string(forKey: "deviceId") {
            deviceId = id
        } else {
            let id = "ipad-" + UUID().uuidString.prefix(8).lowercased()
            d.set(id, forKey: "deviceId")
            deviceId = id
        }
    }

    var appSecret: String {
        get { Keychain.get("app_secret") ?? "" }
        set { Keychain.set("app_secret", newValue) }
    }

    var pin: String {
        get { Keychain.get("settings_pin") ?? "2580" }
        set { Keychain.set("settings_pin", newValue) }
    }

    /// https base URL for HTTP calls, from the host field ("srv.example.com", "https://srv.example.com").
    var httpBase: URL? {
        let h = host.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !h.isEmpty else { return nil }
        let withScheme = h.contains("://") ? h : "https://" + h
        guard var c = URLComponents(string: withScheme) else { return nil }
        if c.scheme == "wss" { c.scheme = "https" }
        if c.scheme == "ws" { c.scheme = "http" }
        c.path = ""
        return c.url
    }

    /// wss URL of the device channel.
    var deviceChannelURL: URL? {
        guard let base = httpBase, var c = URLComponents(url: base, resolvingAgainstBaseURL: false) else { return nil }
        c.scheme = c.scheme == "http" ? "ws" : "wss"
        c.path = "/ws/device"
        return c.url
    }
}

// MARK: - View

struct SettingsView: View {
    @Bindable var model: AppModel
    @Bindable var settings: AppSettings
    @Environment(\.dismiss) private var dismiss

    @State private var unlocked = false
    @State private var pinEntry = ""
    @State private var secretEntry = ""
    @State private var newPin = ""
    @State private var logLines: [String] = []

    var body: some View {
        NavigationStack {
            Group {
                if unlocked { form } else { pinPad }
            }
            .navigationTitle("阿寶設定")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("關閉") { dismiss() } }
            }
        }
        .preferredColorScheme(.dark)
    }

    private var pinPad: some View {
        VStack(spacing: 24) {
            Text("輸入密碼").font(.title2)
            SecureField("····", text: $pinEntry)
                .keyboardType(.numberPad)
                .textContentType(.oneTimeCode)
                .font(.system(size: 40, design: .monospaced))
                .multilineTextAlignment(.center)
                .frame(width: 220)
                .onChange(of: pinEntry) { _, v in
                    if v.count >= 4 {
                        if v == settings.pin { unlocked = true; refreshLog() } else { pinEntry = "" }
                    }
                }
        }
        .padding(40)
    }

    private var form: some View {
        Form {
            Section("伺服器") {
                TextField("主機 (例如 srv2135273.hstgr.cloud)", text: $settings.host)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                SecureField(settings.appSecret.isEmpty ? "App secret" : "App secret（已儲存 \(Log.mask(settings.appSecret))）",
                            text: $secretEntry)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Button("儲存並重新連線") {
                    if !secretEntry.isEmpty { settings.appSecret = secretEntry; secretEntry = "" }
                    model.reconnect()
                }
            }
            Section("功能") {
                Toggle("「喂阿寶」喚醒", isOn: $settings.wakeOn)
                    .onChange(of: settings.wakeOn) { _, _ in model.refreshWake() }
                Toggle("LiveKit 語音", isOn: $settings.liveKitOn)
                Toggle("相機（跌倒相片）", isOn: $settings.cameraOn)
                Toggle("可以打斷阿寶", isOn: $settings.interruptionOn)
                    .onChange(of: settings.interruptionOn) { _, v in model.sendInterruptionSetting(v) }
                Toggle("除錯資訊", isOn: $settings.debugOverlay)
            }
            Section("狀態") {
                LabeledContent("連線", value: model.connection.rawValue)
                LabeledContent("喚醒引擎", value: model.wakeEngineKind.rawValue)
                LabeledContent("語音對話", value: model.inSession ? "進行中" : "冇")
                LabeledContent("Fixed clips", value: model.fixedCacheVersion.isEmpty ? "—" : model.fixedCacheVersion)
                LabeledContent("Device", value: settings.deviceId)
                LabeledContent("版本", value: AppModel.appVersion)
            }
            Section("密碼") {
                SecureField("新密碼（4 位數字）", text: $newPin).keyboardType(.numberPad)
                Button("更改密碼") {
                    if newPin.count == 4, newPin.allSatisfy(\.isNumber) { settings.pin = newPin; newPin = "" }
                }
            }
            Section("記錄（最後 20 行）") {
                Button("更新") { refreshLog() }
                ForEach(Array(logLines.enumerated()), id: \.offset) { _, line in
                    Text(line).font(.caption.monospaced())
                }
            }
        }
    }

    private func refreshLog() { logLines = Log.recent(20) }
}
