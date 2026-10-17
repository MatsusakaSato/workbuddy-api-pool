import Foundation

/// 网关进程状态检测 + 启动/重启/停止。
///
/// 关键点：
/// 1. run.sh 内部是 exec 前台运行，直接调用会挂住调用方，必须 nohup 后台脱离。
/// 2. 状态检测**不用 lsof**（受限环境里从子进程调用会卡住），改用 TCP 直连探测，
///    而且它比进程名更贴近「网关是否真的可用」。
enum GatewayControl {
    enum State {
        case running
        case stopped
        case unknown
    }

    /// 直连 127.0.0.1:8788 尝试建立 TCP 连接，能连上即视为网关在跑。
    /// 用非阻塞 socket + 短超时，绝不阻塞 UI。
    static func state() -> State {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return .unknown }
        defer { close(fd) }

        var addr = sockaddr_in()
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = UInt16(8788).bigEndian
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")

        // 设为非阻塞
        let flags = fcntl(fd, F_GETFL, 0)
        _ = fcntl(fd, F_SETFL, flags | O_NONBLOCK)

        let result = withUnsafePointer(to: &addr) { ptr in
            ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }

        if result == 0 { return .running }
        if errno == EINPROGRESS {
            // 等待最多 0.6 秒确认
            var writeSet = fd_set()
            fdZero(&writeSet)
            fdSet(fd, &writeSet)
            var tv = timeval(tv_sec: 0, tv_usec: 600_000)
            let ready = select(fd + 1, nil, &writeSet, nil, &tv)
            if ready > 0 {
                var err: Int32 = 0
                var len = socklen_t(MemoryLayout<Int32>.size)
                getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &len)
                return err == 0 ? .running : .stopped
            }
            return .stopped
        }
        return .stopped
    }

    /// 智能切换：在跑 → 重启；没跑 → 启动。
    /// 重启 = stop.sh 再 run.sh，且保留 --dev 模式（原来就是 dev 的话继续 dev）。
    @discardableResult
    static func startOrRestart() -> String? {
        let root = ConfigStore.projectRoot()
        let wasDev = isDevMode()
        let current = state()

        if current == .running {
            guard runScript(root: root, script: "stop.sh", extraArgs: []) else {
                return "stop.sh 执行失败"
            }
            // stop.sh 内部已 sleep 1 并校验端口释放，这里再确认
            var stopped = false
            for _ in 0..<20 {
                if state() == .stopped { stopped = true; break }
                Thread.sleep(forTimeInterval: 0.4)
            }
            if !stopped {
                return "网关未能停止（8788 仍被占用）"
            }
        }

        var args: [String] = []
        if wasDev { args = ["--dev"] }
        guard runScript(root: root, script: "run.sh", extraArgs: args) else {
            return "run.sh 启动失败"
        }
        return nil
    }

    /// 是否以 --dev 模式运行（用于重启后保持原模式）。
    /// 只用 pgrep，不碰 lsof。
    private static func isDevMode() -> Bool {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
        task.arguments = ["-f", "gateway\\.server --dev"]
        task.standardOutput = FileHandle.nullDevice
        task.standardError = FileHandle.nullDevice
        do {
            try task.run()
            task.waitUntilExit()
            return task.terminationStatus == 0
        } catch {
            return false
        }
    }

    /// 后台执行脚本：nohup + 重定向，立即返回，绝不阻塞 UI、不弹终端。
    private static func runScript(root: URL, script: String, extraArgs: [String]) -> Bool {
        let scriptPath = root.appendingPathComponent(script).path
        guard FileManager.default.fileExists(atPath: scriptPath) else { return false }

        let logURL = root.appendingPathComponent("state/panel-gateway.log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)

        let args = ([scriptPath] + extraArgs)
            .map { "'\($0)'" }
            .joined(separator: " ")

        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/zsh")
        task.currentDirectoryURL = root
        task.arguments = [
            "-lc",
            "cd '\(root.path)' && nohup \(args) >> '\(logURL.path)' 2>&1 &",
        ]
        do {
            try task.run()
            task.waitUntilExit()
            return task.terminationStatus == 0
        } catch {
            return false
        }
    }
}

// MARK: - fd_set 辅助（Swift 里 Darwin.fd_set 用起来别扭）

private func fdZero(_ set: inout fd_set) {
    memset(&set, 0, MemoryLayout<fd_set>.size)
}

private func fdSet(_ fd: Int32, _ set: inout fd_set) {
    withUnsafeMutablePointer(to: &set) { ptr in
        ptr.withMemoryRebound(to: Int32.self, capacity: 1) { ints in
            ints[Int(fd) / 32] |= Int32(1 << (fd % 32))
        }
    }
}
