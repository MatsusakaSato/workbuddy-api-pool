import AppKit
import SwiftUI

/// 显式入口，配合 -parse-as-library 多文件编译。
@main
struct QuickPanelMain {
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)   // 不出现在 Dock，不抢焦点
        app.run()
    }
}
