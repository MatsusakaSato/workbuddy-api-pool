import AppKit
import SwiftUI

// MARK: - 毛玻璃背景

/// 真正的透明毛玻璃：NSVisualEffectView 直接采样窗口背后的桌面内容。
/// blendingMode 必须是 .behindWindow —— 它让模糊吃掉窗口后面的真实画面，
/// 这才是 macOS 原生「毛玻璃」；.withinWindow 只会模糊窗口自身内容，等于没效果。
struct VisualEffectView: NSViewRepresentable {
    var material: NSVisualEffectView.Material = .hudWindow
    var blendingMode: NSVisualEffectView.BlendingMode = .behindWindow
    var state: NSVisualEffectView.State = .active

    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material = material
        view.blendingMode = blendingMode
        view.state = state
        view.isEmphasized = false
        return view
    }

    func updateNSView(_ nsView: NSVisualEffectView, context: Context) {
        nsView.material = material
        nsView.blendingMode = blendingMode
        nsView.state = state
    }
}

// MARK: - 主题

/// 悬浮面板配色。背景是动态毛玻璃，所以文字统一用自适应语义色，
/// 不写死白/黑，避免在浅色或深色桌面上都出现看不清的情况。
enum Theme {
    static let panelInset: CGFloat = 12
    static let cornerRadius: CGFloat = 20

    // MARK: 文字
    // 毛玻璃会透出任意桌面内容，语义色（primary/secondary）在亮壁纸下会变成浅灰，
    // 直接看不清。这里全部用固定深色，并对关键文字加一层极淡白衬底。
    /// 主文字：近黑，用于日期、按钮标题、数值。
    static let title = Color(white: 0.10)
    /// 次要文字：中深灰，用于指标标签、账号名。不要再用 lightGray 级亮度。
    static let label = Color(white: 0.28)
    /// 更弱的辅助文字（账号积分等），仍保持可读。
    static let labelSoft = Color(white: 0.38)

    static let divider = Color(white: 0.10).opacity(0.10)

    // MARK: 数据指标色
    // 相比之前整体压暗一档：亮色在半透明底上会发飘、对比度不够。
    static let metricToken = Color(red: 0.10, green: 0.38, blue: 0.78)
    static let metricCredit = Color(red: 0.72, green: 0.34, blue: 0.00)
    static let metricRemain = Color(red: 0.10, green: 0.52, blue: 0.30)
    /// 余额第二个桶（平台奖励）：同色系压暗一档，表示"同为资产、但优先级更低"。
    /// 不用灰色：灰色在"金额"这个语境里会被读成"无效/未知"。
    static let metricRemainSoft = Color(red: 0.10, green: 0.38, blue: 0.24)
}

// MARK: - 无边框悬浮窗

final class PanelWindow: NSPanel {
    init(contentRect: NSRect) {
        super.init(
            contentRect: contentRect,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        isFloatingPanel = true
        level = .floating                  // 由钉子按钮切换
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        isMovableByWindowBackground = true
        isOpaque = false
        backgroundColor = .clear           // 关键：窗口本身透明，才能透出毛玻璃
        hasShadow = true
        titleVisibility = .hidden
        titlebarAppearsTransparent = true
        hidesOnDeactivate = false
        becomesKeyOnlyIfNeeded = true
        isReleasedWhenClosed = false
    }

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    /// 钉子切换：置顶 = .floating（浮于普通窗口之上），取消 = .normal。
    func setAlwaysOnTop(_ on: Bool) {
        level = on ? .floating : .normal
    }
}

// MARK: - 根视图

struct PanelRootView: View {
    @ObservedObject var model: PanelModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            OverviewView(
                snapshot: model.snapshot,
                config: model.config,
                isRefreshing: model.isRefreshing,
                lastError: model.lastError,
                onRefresh: { model.refresh() },
                onToggleAccounts: { model.toggleAccounts() },
                accountsExpanded: model.accountsExpanded,
                isPinned: model.isPinned,
                onTogglePin: { model.togglePin() }
            )
            Divider().overlay(Theme.divider)
            LauncherView(
                gatewayRunning: model.gatewayRunning,
                busy: model.busyAction,
                onOpenDashboard: { model.openDashboard() },
                onToggleGateway: { model.toggleGateway() }
            )
        }
        .padding(Theme.panelInset + 2)
        .frame(width: CGFloat(model.config.panel.width))
        .fixedSize(horizontal: false, vertical: true)
        .background(panelBackground)
    }

    /// 纯毛玻璃：只有一层视觉特效视图，不加任何不透明白色覆盖层。
    private var panelBackground: some View {
        ZStack {
            VisualEffectView(material: .sidebar, blendingMode: .behindWindow)
            // 白色薄纱：让深色文字在深壁纸上也读得清。
            // 0.30 是折中值——能明显提亮，仍能透出背后的画面与模糊。
            Color.white.opacity(0.30)
        }
        .clipShape(RoundedRectangle(cornerRadius: Theme.cornerRadius, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: Theme.cornerRadius, style: .continuous)
                .strokeBorder(Color.white.opacity(0.18), lineWidth: 1)
        )
    }
}

// MARK: - 应用委托

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var window: PanelWindow?
    private let model = PanelModel()

    func applicationDidFinishLaunching(_ notification: Notification) {
        ConfigStore.writeDefaultIfMissing()
        let config = ConfigStore.load()

        // 把窗口控制器交给 model，钉子按钮才能改 level
        model.attachWindowController { [weak self] pinned in
            self?.window?.setAlwaysOnTop(pinned)
        }

        let root = PanelRootView(model: model)
        let hosting = NSHostingView(rootView: root)
        hosting.wantsLayer = true
        hosting.layer?.backgroundColor = NSColor.clear.cgColor

        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let width = CGFloat(config.panel.width)
        let margin = CGFloat(config.panel.margin)

        // 先用一个合理占位高度创建窗口，避免 0 尺寸导致布局异常
        let placeholder = NSRect(x: 0, y: 0, width: width, height: 260)
        let panel = PanelWindow(contentRect: placeholder)
        panel.contentView = hosting
        panel.orderFrontRegardless()
        window = panel

        // 等 SwiftUI 完成首帧布局后再按真实内容尺寸重设窗口大小并摆位
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.resizeAndPosition(hosting: hosting,
                                   configCorner: config.panel.corner,
                                   margin: margin,
                                   screen: screen,
                                   width: width)
        }

        // 初始置顶状态跟随配置
        model.applyInitialPin(config.panel.pinnedByDefault ?? true)
    }

    /// 按 SwiftUI 内容真实高度重设窗口尺寸，并吸附到配置的屏幕角落。
    private func resizeAndPosition(hosting: NSHostingView<PanelRootView>,
                                   configCorner: String,
                                   margin: CGFloat,
                                   screen: NSRect,
                                   width: CGFloat) {
        hosting.layoutSubtreeIfNeeded()
        var size = hosting.fittingSize
        if size.height < 60 || size.height.isNaN { size.height = 260 }
        if size.width < 100 || size.width.isNaN { size.width = width }

        let height = size.height
        var origin = CGPoint(x: screen.maxX - width - margin, y: screen.maxY - height - margin)
        switch configCorner {
        case "top-left": origin = CGPoint(x: screen.minX + margin, y: screen.maxY - height - margin)
        case "bottom-right": origin = CGPoint(x: screen.maxX - width - margin, y: screen.minY + margin)
        case "bottom-left": origin = CGPoint(x: screen.minX + margin, y: screen.minY + margin)
        case "center": origin = CGPoint(x: screen.midX - width / 2, y: screen.midY - height / 2)
        default: break
        }
        window?.setFrame(NSRect(origin: origin, size: NSSize(width: width, height: height)),
                         display: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }
}
