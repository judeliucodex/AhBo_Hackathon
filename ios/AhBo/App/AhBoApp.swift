// AhBoApp.swift — app entry (PRD §25.2, §25.7, §23.9). The screen is the face, full-bleed, always on:
// idle timer off, status bar and home indicator hidden (persistentSystemOverlays), dark, no system UI.

import SwiftUI

@main
struct AhBoApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView(model: model)
                .onAppear { model.start() }
        }
    }
}

struct RootView: View {
    @Bindable var model: AppModel

    var body: some View {
        ZStack(alignment: .topLeading) {
            Color.black.ignoresSafeArea()
            FaceWebView(controller: model.face)
                .ignoresSafeArea()
            if model.settings.debugOverlay {
                DebugOverlay(model: model)
                    .padding(12)
                    .allowsHitTesting(false)
            }
        }
        .statusBarHidden(true)
        .persistentSystemOverlays(.hidden)
        .preferredColorScheme(.dark)
        .sheet(isPresented: $model.showSettings) {
            SettingsView(model: model, settings: model.settings)
        }
    }
}

/// Owner-only debug text (off by default; toggled in the hidden settings).
struct DebugOverlay: View {
    let model: AppModel

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { _ in
            VStack(alignment: .leading, spacing: 2) {
                Text("\(model.connection.rawValue) · wake \(model.wakeEngineKind.rawValue) · \(model.inSession ? "session" : "idle")")
                ForEach(Array(Log.recent(5).enumerated()), id: \.offset) { _, line in Text(line) }
            }
            .font(.caption2.monospaced())
            .foregroundStyle(.gray)
        }
    }
}
