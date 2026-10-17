import Foundation

/// 面板配置：从 config.json 读取，未找到时回落到内置默认值。
struct PanelConfig: Codable {
    struct Panel: Codable {
        var width: Double
        var corner: String        // top-right / top-left / bottom-right / bottom-left / center
        var margin: Double
        var refreshSeconds: Double
        var showAccounts: Bool
        var pinnedByDefault: Bool? = true

        static let `default` = Panel(
            width: 320,
            corner: "top-right",
            margin: 16,
            refreshSeconds: 30,
            showAccounts: true,
            pinnedByDefault: true
        )
    }

    struct Card: Codable, Identifiable {
        var id: String { "\(type)-\(title)" }
        var title: String
        var type: String          // app / url / folder / script
        var target: String
        var subtitle: String? = nil
    }

    var panel: Panel
    var cards: [Card]

    static let `default` = PanelConfig(
        panel: .default,
        cards: [
            Card(title: "WorkBuddy", type: "app", target: "WorkBuddy"),
            Card(title: "DeepSeek Harness", type: "app", target: "DeepSeek Harness"),
            Card(title: "VS Code", type: "app", target: "Visual Studio Code"),
            Card(title: "网关面板", type: "url", target: "http://127.0.0.1:8788/"),
            Card(title: "号池目录", type: "folder", target: "."),
            Card(title: "重启网关", type: "script", target: "run.sh"),
        ]
    )
}

enum ConfigStore {
    /// 项目根目录：从可执行文件向上找，直到包含 gateway/ 与 state/ 的目录。
    ///
    /// 兜底**不能**用 `#filePath`：那是编译机上的源码路径，换台机器/换个目录就指向虚空，
    /// 而且 `panel/Sources` 上三级是 `panel`，连放 config.json 的那层都到不了。
    /// 改成从**当前工作目录**再往上找一轮 —— install.sh 是从项目内启动的。
    static func projectRoot() -> URL {
        func isRoot(_ dir: URL) -> Bool {
            FileManager.default.fileExists(atPath: dir.appendingPathComponent("gateway").path)
                && FileManager.default.fileExists(atPath: dir.appendingPathComponent("state").path)
        }

        var dir = Bundle.main.bundleURL
        for _ in 0..<6 {
            dir = dir.deletingLastPathComponent()
            if isRoot(dir) { return dir }
        }

        var cwd = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        for _ in 0..<6 {
            if isRoot(cwd) { return cwd }
            cwd = cwd.deletingLastPathComponent()
        }

        // 实在找不到：退到 bundle 旁边，至少 config.json 的读写路径是稳定的。
        return Bundle.main.bundleURL.deletingLastPathComponent()
    }

    static var configURL: URL {
        projectRoot().appendingPathComponent("panel/config.json")
    }

    static func load() -> PanelConfig {
        guard let data = try? Data(contentsOf: configURL),
              let cfg = try? JSONDecoder().decode(PanelConfig.self, from: data) else {
            return .default
        }
        return cfg
    }

    static func writeDefaultIfMissing() {
        guard !FileManager.default.fileExists(atPath: configURL.path) else { return }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        if let data = try? encoder.encode(PanelConfig.default) {
            try? data.write(to: configURL)
        }
    }

    /// 从项目 .env 读取 GATEWAY_API_KEY；只读不缓存到别处。
    static func gatewayAPIKey() -> String? {
        let envURL = projectRoot().appendingPathComponent(".env")
        guard let text = try? String(contentsOf: envURL, encoding: .utf8) else { return nil }
        for line in text.split(separator: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard trimmed.hasPrefix("GATEWAY_API_KEY=") else { continue }
            var value = String(trimmed.dropFirst("GATEWAY_API_KEY=".count))
            value = value.trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
            return value.isEmpty ? nil : value
        }
        return nil
    }
}
