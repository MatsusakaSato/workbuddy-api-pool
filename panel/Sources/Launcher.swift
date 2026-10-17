import AppKit
import Foundation

/// 把卡片点击翻译成实际动作。任何一类启动都不应把终端窗口带到前台。
enum Launcher {
    static func open(_ card: PanelConfig.Card) -> String? {
        switch card.type {
        case "app":
            return openApp(card.target)
        case "url":
            return openURL(card.target)
        case "folder":
            return openFolder(card.target)
        case "script":
            return runScript(card.target)
        default:
            return "未知卡片类型：\(card.type)"
        }
    }

    // MARK: - 各类目标

    private static func resolvePath(_ raw: String) -> URL {
        let expanded = (raw as NSString).expandingTildeInPath
        if expanded.hasPrefix("/") {
            return URL(fileURLWithPath: expanded)
        }
        return ConfigStore.projectRoot().appendingPathComponent(expanded)
    }

    private static func openApp(_ name: String) -> String? {
        // 先按 App 名找，找不着再按路径找
        let candidates = [
            "/Applications/\(name).app",
            NSHomeDirectory() + "/Applications/\(name).app",
        ]
        for path in candidates where FileManager.default.fileExists(atPath: path) {
            if NSWorkspace.shared.open(URL(fileURLWithPath: path)) { return nil }
        }
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        task.arguments = ["-a", name]
        do {
            try task.run()
            task.waitUntilExit()
            return task.terminationStatus == 0 ? nil : "无法启动应用「\(name)」"
        } catch {
            return "无法启动应用「\(name)」：\(error.localizedDescription)"
        }
    }

    private static func openURL(_ raw: String) -> String? {
        guard let url = URL(string: raw) else { return "无效网址：\(raw)" }
        return NSWorkspace.shared.open(url) ? nil : "无法打开网址：\(raw)"
    }

    private static func openFolder(_ raw: String) -> String? {
        let url = resolvePath(raw)
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDir), isDir.boolValue else {
            return "目录不存在：\(url.path)"
        }
        return NSWorkspace.shared.open(url) ? nil : "无法打开目录：\(url.path)"
    }

    /// 跑脚本并彻底脱离当前进程会话：setsid 让子进程脱离控制终端，
    /// 输出重定向到项目 state/panel-scripts.log，绝不弹终端窗口。
    private static func runScript(_ raw: String) -> String? {
        let script = resolvePath(raw)
        guard FileManager.default.fileExists(atPath: script.path) else {
            return "脚本不存在：\(script.path)"
        }
        let root = ConfigStore.projectRoot()
        let logURL = root.appendingPathComponent("state/panel-scripts.log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)

        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/zsh")
        task.currentDirectoryURL = root
        // setsid 在 macOS 上没有独立二进制；用 nohup + 后台重定向实现脱离。
        task.arguments = [
            "-lc",
            "cd '\(root.path)' && nohup '\(script.path)' >> '\(logURL.path)' 2>&1 &",
        ]
        do {
            try task.run()
            task.waitUntilExit()
            return task.terminationStatus == 0 ? nil : "脚本启动失败（退出码 \(task.terminationStatus)）"
        } catch {
            return "脚本启动失败：\(error.localizedDescription)"
        }
    }
}
