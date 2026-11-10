// JyutpingMatcherTests.swift — XCTest for the Stage-2 wake matcher (PRD §25.6), driven by the SAME fixture as
// the Python twin: brain/config/wake_cases.yaml, bundled into this test target straight from its single
// source (project.yml). Each of its `key: [...]` lines is a JSON array (kept so by a Python test,
// brain/tests/wake/test_wake_matcher.py::test_cases_yaml_is_swift_parsable), so no YAML library is needed.

import XCTest

final class JyutpingMatcherTests: XCTestCase {
    private var bundle: Bundle { Bundle(for: JyutpingMatcherTests.self) }

    private func text(_ name: String, _ ext: String) throws -> String {
        let url = try XCTUnwrap(bundle.url(forResource: name, withExtension: ext), "\(name).\(ext) not bundled")
        return try String(contentsOf: url, encoding: .utf8)
    }

    private func cases() throws -> [String: [String]] {
        var out: [String: [String]] = [:]
        for raw in try text("wake_cases", "yaml").split(whereSeparator: \.isNewline) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") { continue }
            guard let colon = line.firstIndex(of: ":") else { continue }
            let key = String(line[..<colon]).trimmingCharacters(in: .whitespaces)
            let json = Data(line[line.index(after: colon)...].utf8)
            out[key] = try JSONDecoder().decode([String].self, from: json)
        }
        return out
    }

    private func matcher(_ tweak: (inout WakeMatchConfig) -> Void = { _ in }) throws -> JyutpingMatcher {
        var cfg = WakeMatchConfig.parseYAML(try text("wake_match", "yaml"))
        tweak(&cfg)
        let url = try XCTUnwrap(bundle.url(forResource: "wake_homophones", withExtension: "json"))
        return try JyutpingMatcher(tableJSON: try Data(contentsOf: url), config: cfg)
    }

    func testFixtureLoads() throws {
        let c = try cases()
        XCTAssertFalse(c["accept", default: []].isEmpty)
        XCTAssertFalse(c["reject", default: []].isEmpty)
    }

    func testConfigFromYAML() throws {
        let cfg = WakeMatchConfig.parseYAML(try text("wake_match", "yaml"))
        XCTAssertTrue(cfg.requireWai)
        XCTAssertTrue(cfg.requireTwoUpdates)
        XCTAssertEqual(cfg.utteranceStartSilenceMs, 300)
        XCTAssertEqual(cfg.cooldownAfterRejectS, 5)
        XCTAssertTrue(cfg.allowMissingAa)
        XCTAssertFalse(cfg.plainAbouWakes)
    }

    func testAccept() throws {
        let m = try matcher()
        for t in try cases()["accept", default: []] { XCTAssertTrue(m.match(t).ok, "should wake: \(t)") }
    }

    func testReject() throws {
        let m = try matcher()
        for t in try cases()["reject", default: []] { XCTAssertFalse(m.match(t).ok, "should not wake: \(t)") }
    }

    func testRemainder() throws {
        let m = try matcher()
        let expected: [(String, String)] = [("喂阿寶", ""), ("喂，阿寶", ""), ("喂 阿寶", ""), ("喂寶", ""),
                                             ("喂阿寶，今日有咩做呀？", "今日有咩做呀？"),
                                             ("威阿寶 我幾時食藥呀", "我幾時食藥呀"), ("  喂阿寶。", "")]
        for (t, r) in expected {
            let res = m.match(t)
            XCTAssertTrue(res.ok, t)
            XCTAssertEqual(res.remainder, r, t)
        }
    }

    func testRules() throws {
        let m = try matcher()
        XCTAssertEqual(m.match("喂寶").rule, "missing_aa")
        XCTAssertEqual(m.match("喂喂阿寶").rule, "filler")
        XCTAssertEqual(m.match("喂亞寶").rule, "full")
        XCTAssertEqual(m.match("喂阿寶").rule, "exact")
        XCTAssertFalse(m.match("喂阿寶寶").ok)     // reduplication guard
    }

    func testSwitches() throws {
        XCTAssertFalse(try matcher().match("阿寶，你好").ok)
        let plain = try matcher { $0.plainAbouWakes = true }.match("阿寶，你好")
        XCTAssertTrue(plain.ok)
        XCTAssertEqual(plain.remainder, "你好")
        XCTAssertFalse(try matcher { $0.allowMissingAa = false }.match("喂寶").ok)
    }

    func testServerOverride() {
        let c = WakeMatchConfig().merged(["plain_abou_wakes": true, "cooldown_after_reject_s": 7])
        XCTAssertTrue(c.plainAbouWakes)
        XCTAssertEqual(c.cooldownAfterRejectS, 7)
    }
}
