import SwiftUI

/// 启动区：两个入口，统一设计语言（半透明胶囊 + 描边 + 图标 + 文字）。
struct LauncherView: View {
    let gatewayRunning: Bool
    let busy: String?
    let onOpenDashboard: () -> Void
    let onToggleGateway: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            PillButton(
                title: "网关面板",
                icon: "rectangle.grid.2x2.fill",
                accent: Theme.metricToken,
                busy: busy == "dashboard",
                action: onOpenDashboard
            )

            PillButton(
                title: gatewayRunning ? "重启网关" : "启动网关",
                icon: gatewayRunning ? "arrow.triangle.2.circlepath" : "play.fill",
                accent: gatewayRunning ? Theme.metricCredit : Theme.metricRemain,
                busy: busy == "gateway",
                action: onToggleGateway
            )
        }
    }
}

/// 统一的胶囊按钮：毛玻璃上通用的半透明底 + 彩色描边 + 彩色图标。
/// 两个入口只在 accent 颜色上区分，形态完全一致。
struct PillButton: View {
    let title: String
    let icon: String
    let accent: Color
    var busy: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if busy {
                    ProgressView()
                        .controlSize(.mini)
                        .scaleEffect(0.62)
                        .frame(width: 13, height: 13)
                        .tint(accent)
                } else {
                    Image(systemName: icon)
                        .font(.system(size: 11.5, weight: .bold))
                        .foregroundStyle(accent)
                        .frame(width: 13)
                        .symbolRenderingMode(.hierarchical)
                }
                Text(title)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.title)
                    .lineLimit(1)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, minHeight: 34)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(Color.white.opacity(0.22))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .strokeBorder(accent.opacity(0.45), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .disabled(busy)
    }
}

/// 小圆形图标按钮（钉子等）：毛玻璃上的轻量控件。
struct IconBadgeButton: View {
    let icon: String
    let active: Bool
    let help: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: icon)
                .font(.system(size: 11, weight: .bold))
                .foregroundStyle(active ? Theme.metricToken : Theme.label)
                .rotationEffect(.degrees(active ? 0 : 45))
                .frame(width: 22, height: 22)
                .background(
                    Circle().fill(
                        active
                            ? Theme.metricToken.opacity(0.20)
                            : Color.primary.opacity(0.06)
                    )
                )
                .overlay(
                    Circle().strokeBorder(
                        active ? Theme.metricToken.opacity(0.5) : Color.primary.opacity(0.10),
                        lineWidth: 1
                    )
                )
        }
        .buttonStyle(.plain)
        .help(help)
    }
}
