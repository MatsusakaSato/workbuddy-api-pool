import SwiftUI

/// 概览区：今日 token、今日积分、剩余积分 + 近 7 日曲线 + 账号明细。
struct OverviewView: View {
    let snapshot: GatewaySnapshot
    let config: PanelConfig
    let isRefreshing: Bool
    let lastError: String?
    let onRefresh: () -> Void
    let onToggleAccounts: () -> Void
    let accountsExpanded: Bool
    let isPinned: Bool
    let onTogglePin: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            header
            metricRow(label: "今日 Token", value: formattedTokens(snapshot.todayTokens), color: Theme.metricToken)
            metricRow(label: "今日积分", value: String(format: "-%.2f", snapshot.todayCredits), color: Theme.metricCredit)
            // 余额拆成三行：合计 + 两个桶。合成一行会丢掉"这钱会不会过期"这层信息 ——
            // 基础套餐月底清零、平台奖励过期晚得多，同一个合计可能是两种处境。
            metricRow(label: "剩余积分", value: "≈ \(formattedInt(snapshot.remainingCredits))", color: Theme.metricRemain)
            splitRow(label: "基础套餐", value: formattedDecimal(snapshot.remainingBaseCredits), hint: "月底清零 · 优先消耗", color: Theme.metricRemain)
            splitRow(label: "平台奖励", value: formattedDecimal(snapshot.remainingRewardCredits), hint: "一次性赠送包", color: Theme.metricRemainSoft)

            // 有账号还没拉到余额时，合计只是"已知部分的和"，必须标出来 ——
            // 否则用户会以为余额被少算了（这正是"数字看起来不对"的来源）。
            if snapshot.unknownCreditAccounts > 0 {
                Text("含 \(snapshot.unknownCreditAccounts) 个账号余额未拉取，合计偏低")
                    .font(.system(size: 9))
                    .foregroundStyle(Theme.metricCredit)
                    .padding(.leading, 8)
            }

            if config.panel.showAccounts && !snapshot.accounts.isEmpty {
                accountSection
            }
            if !snapshot.last7Days.isEmpty {
                SparklineView(points: snapshot.last7Days)
                    .frame(height: 34)
                    .padding(.top, 2)
            }
        }
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 6) {
            Text(dateString())
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(Theme.title)
            Spacer()
            if let error = lastError {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 9))
                    .foregroundStyle(Theme.metricCredit)
                    .help(error)
            }
            IconBadgeButton(
                icon: "pin.fill",
                active: isPinned,
                help: isPinned ? "已置顶于所有窗口之上（点击取消）" : "置顶于所有窗口之上",
                action: onTogglePin
            )
            IconBadgeButton(
                icon: "arrow.clockwise",
                active: false,
                help: "立即刷新",
                action: onRefresh
            )
            .opacity(isRefreshing ? 0.5 : 1)
            .disabled(isRefreshing)
        }
    }

    private func metricRow(label: String, value: String, color: Color) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(Theme.label)
            Spacer()
            Text(value)
                .font(.system(size: 16, weight: .semibold, design: .rounded))
                .foregroundStyle(color)
        }
    }

    /// 余额拆分行：比主指标低一档（小字号、次级色），让"合计"仍是视觉主角，
    /// 但两个桶的数字**始终在界面上**，而不是藏进 tooltip。
    private func splitRow(label: String, value: String, hint: String, color: Color) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 4) {
            Text(label)
                .font(.system(size: 10, weight: .medium))
                .foregroundStyle(Theme.labelSoft)
            Text(hint)
                .font(.system(size: 9))
                .foregroundStyle(Theme.labelSoft.opacity(0.7))
            Spacer()
            Text(value)
                .font(.system(size: 11, weight: .semibold, design: .rounded))
                .foregroundStyle(color)
        }
        .padding(.leading, 8)
    }

    private var accountSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Button(action: onToggleAccounts) {
                HStack(spacing: 4) {
                    Image(systemName: accountsExpanded ? "chevron.down" : "chevron.right")
                        .font(.system(size: 8, weight: .bold))
                    Text("\(snapshot.accounts.count) 个账号")
                        .font(.system(size: 10, weight: .medium))
                }
                .foregroundStyle(Theme.label)
            }
            .buttonStyle(.plain)

            if accountsExpanded {
                ForEach(snapshot.accounts) { acct in
                    HStack(spacing: 6) {
                        Circle()
                            .fill(acct.available ? Color.green : Color.orange)
                            .frame(width: 5, height: 5)
                        Text(acct.nickname)
                            .font(.system(size: 10, weight: .medium))
                            .foregroundStyle(Theme.label)
                            .lineLimit(1)
                        Spacer()
                        if let base = acct.baseCredits, let reward = acct.rewardCredits {
                            Text("基 \(formattedDecimal(base))")
                                .font(.system(size: 9, weight: .medium, design: .rounded))
                                .foregroundStyle(Theme.labelSoft)
                                .help("基础套餐积分（月底清零，轮换优先消耗）")
                            Text("奖 \(formattedDecimal(reward))")
                                .font(.system(size: 9, weight: .medium, design: .rounded))
                                .foregroundStyle(Theme.labelSoft.opacity(0.8))
                                .help("平台奖励积分（一次性赠送包，过期较晚）")
                        }
                        Text(acct.credits.map { formattedDecimal($0) } ?? "—")
                            .font(.system(size: 10, weight: .semibold, design: .rounded))
                            .foregroundStyle(Theme.labelSoft)
                    }
                }
            }
        }
    }

    private func dateString() -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "zh_CN")
        f.dateFormat = "M月d日 EEEE"
        return f.string(from: Date())
    }

    private func formattedTokens(_ n: Int) -> String {
        switch n {
        case ..<1_000: return "\(n)"
        case ..<1_000_000: return String(format: "%.1fK", Double(n) / 1_000)
        case ..<1_000_000_000: return String(format: "%.2fM", Double(n) / 1_000_000)
        default: return String(format: "%.2fB", Double(n) / 1_000_000_000)
        }
    }

    private func formattedInt(_ n: Double) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.groupingSeparator = ","
        f.maximumFractionDigits = 0
        return f.string(from: NSNumber(value: n)) ?? "\(Int(n))"
    }

    /// 余额带 1 位小数：上游给的是 831.53000015，两个桶各向下取整会让合计对不上。
    private func formattedDecimal(_ n: Double) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.groupingSeparator = ","
        f.minimumFractionDigits = 0
        f.maximumFractionDigits = 1
        return f.string(from: NSNumber(value: n)) ?? String(format: "%.1f", n)
    }
}

/// 近 7 日 token 迷你折线。
///
/// 归一化用"从数据起点算起"而不是绝对 0：号池往往只有两三天有数据，
/// 其余天是 0；从 0 画起会让曲线变成"贴底一条线 + 末尾一根尖刺"，
/// 看起来像没数据。这里以区间最小值做基线，让有数据的那几天撑满高度。
struct SparklineView: View {
    let points: [DayPoint]

    var body: some View {
        GeometryReader { geo in
            let pts = normalized(in: geo.size)
            ZStack(alignment: .bottomLeading) {
                Path { path in
                    for (i, pt) in pts.enumerated() {
                        if i == 0 { path.move(to: pt) } else { path.addLine(to: pt) }
                    }
                }
                .stroke(
                    LinearGradient(colors: [.cyan.opacity(0.9), .blue.opacity(0.6)], startPoint: .top, endPoint: .bottom),
                    style: StrokeStyle(lineWidth: 1.6, lineCap: .round, lineJoin: .round)
                )

                // 当日点：让"最新的一个值"看得见，而不是只能猜线的末端。
                if let last = pts.last, pts.count > 1 {
                    Circle()
                        .fill(Color.cyan)
                        .frame(width: 3.5, height: 3.5)
                        .position(x: last.x, y: last.y)
                }

                Path { path in
                    guard let first = pts.first, let last = pts.last else { return }
                    path.move(to: CGPoint(x: first.x, y: geo.size.height))
                    path.addLine(to: first)
                    for pt in pts.dropFirst() { path.addLine(to: pt) }
                    path.addLine(to: CGPoint(x: last.x, y: geo.size.height))
                    path.closeSubpath()
                }
                .fill(LinearGradient(colors: [.cyan.opacity(0.18), .clear], startPoint: .top, endPoint: .bottom))
            }
        }
    }

    /// 把每日 token 映射成坐标点。
    /// 基线取区间最小值而非绝对 0：号池往往只有两三天有数据，其余是 0，
    /// 从 0 画起会变成"贴底一条线 + 末尾一根尖刺"，看起来像没数据。
    private func normalized(in size: CGSize) -> [CGPoint] {
        guard !points.isEmpty else { return [] }
        let values = points.map { Double($0.tokens) }
        let peak = values.max() ?? 0
        let base = values.min() ?? 0
        let span = peak - base
        let count = max(values.count - 1, 1)
        let w = size.width
        let h = size.height
        return values.enumerated().map { index, value in
            let x = w * CGFloat(index) / CGFloat(count)
            // 全平时（全 0 或值都相同）退化成一条居中直线，避免除零与满屏尖刺。
            let ratio = span > 0 ? CGFloat((value - base) / span) : 0.5
            let y = h - ratio * (h - 4) - 2
            return CGPoint(x: x, y: y)
        }
    }
}
