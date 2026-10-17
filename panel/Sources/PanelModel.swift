import Foundation
import SwiftUI
import AppKit

/// 面板状态：配置、网关快照、刷新定时器、网关启停。
/// 注意：本机仅 Command Line Tools，无 SwiftUIMacros，全程避免 @State/@StateObject 等宏。
final class PanelModel: ObservableObject {
    @Published private(set) var config: PanelConfig
    @Published private(set) var snapshot = GatewaySnapshot()
    @Published private(set) var isRefreshing = false
    @Published private(set) var lastError: String?
    @Published private(set) var accountsExpanded = false
    /// 网关是否在 8788 监听，决定按钮显示「启动」还是「重启」。
    @Published private(set) var gatewayRunning = false
    /// 正在执行的动作标识（gateway），用于按钮 loading。
    @Published private(set) var busyAction: String?
    /// 是否置顶于其他窗口之上（钉子按钮）。
    @Published private(set) var isPinned = true

    /// 由 AppDelegate 注入：把置顶状态应用到真正的 NSPanel。
    private var applyPin: ((Bool) -> Void)?

    private var timer: Timer?
    private let queue = DispatchQueue(label: "quickpanel.refresh")

    init() {
        config = ConfigStore.load()
        refreshGatewayState()
        refresh()
        scheduleRefresh()
    }

    deinit {
        timer?.invalidate()
    }

    private func scheduleRefresh() {
        timer?.invalidate()
        let interval = max(config.panel.refreshSeconds, 5)
        let t = Timer(timeInterval: interval, repeats: true) { [weak self] _ in
            self?.refresh()
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    /// 拉取用量数据；顺带刷新网关在线状态。
    ///
    /// 防重入必须**同步**判断：旧实现把 `guard !isRefreshing; isRefreshing = true`
    /// 丢进 `DispatchQueue.main.async`，而请求立刻发车。于是同一轮里连续两次
    /// 调用（定时器 + 点击刷新）都会通过守卫，两个响应互相覆盖，
    /// 后到的那次还会把前一次的 `isRefreshing = false` 覆盖掉。
    func refresh() {
        // 只从主线程读写 isRefreshing，保证守卫与置位不可分割。
        if Thread.isMainThread {
            performRefreshIfIdle()
        } else {
            DispatchQueue.main.async { [weak self] in self?.performRefreshIfIdle() }
        }
    }

    private func performRefreshIfIdle() {
        dispatchPrecondition(condition: .onQueue(.main))
        guard !isRefreshing else { return }
        isRefreshing = true

        let previous = snapshot
        queue.async { [weak self] in
            let (snap, error) = GatewayClient.fetch(previous: previous)
            // state() 走进程探测，可能阻塞，放到后台队列；
            // 否则主线程卡顿，而刷新间隔只有几十秒。
            let running = snap.reachable || GatewayControl.state() == .running
            DispatchQueue.main.async {
                guard let self else { return }
                self.snapshot = snap
                self.lastError = error
                self.gatewayRunning = running
                self.isRefreshing = false
            }
        }
    }

    func refreshGatewayState() {
        queue.async { [weak self] in
            let running = GatewayControl.state() == .running
            DispatchQueue.main.async {
                self?.gatewayRunning = running
            }
        }
    }

    func toggleAccounts() {
        accountsExpanded.toggle()
    }

    /// AppDelegate 在窗口创建后注入回调。
    func attachWindowController(_ apply: @escaping (Bool) -> Void) {
        applyPin = apply
    }

    /// 启动时按配置初始化置顶状态（默认置顶）。
    func applyInitialPin(_ pinned: Bool) {
        isPinned = pinned
        applyPin?(pinned)
    }

    /// 钉子：切换 置顶 / 普通层级。
    func togglePin() {
        isPinned.toggle()
        applyPin?(isPinned)
    }

    /// 网关面板：打开后台网页。
    func openDashboard() {
        Task { @MainActor [weak self] in
            self?.busyAction = "dashboard"
            _ = NSWorkspace.shared.open(GatewayClient.dashboardURL)
            try? await Task.sleep(nanoseconds: 600_000_000)
            self?.busyAction = nil
        }
    }

    /// 启动/重启网关：先停再起（运行中时），或直接起（未运行）。
    func toggleGateway() {
        guard busyAction == nil else { return }
        busyAction = "gateway"
        gateQueue.async { [weak self] in
            let error = GatewayControl.startOrRestart()
            if let error {
                NSLog("QuickPanel 网关操作失败：%@", error)
            }
            // 等网关起来后再回读状态；run.sh 起服务大约 1~2 秒
            var running = false
            for _ in 0..<20 {
                Thread.sleep(forTimeInterval: 0.5)
                if GatewayControl.state() == .running {
                    running = true
                    break
                }
            }
            DispatchQueue.main.async {
                guard let self else { return }
                self.gatewayRunning = running
                self.busyAction = nil
                if running {
                    self.refresh()
                } else {
                    self.lastError = error ?? "网关未能在 10 秒内启动"
                }
            }
        }
    }

    private let gateQueue = DispatchQueue(label: "quickpanel.gateway")
}
