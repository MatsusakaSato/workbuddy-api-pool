import Foundation

/// 网关快照：概览区需要的全部数据。
struct GatewaySnapshot {
    var todayTokens: Int = 0
    var todayCredits: Double = 0
    var todayRequests: Int = 0
    /// 合计余额 = 基础套餐 + 平台奖励（只累加**已知**余额的账号）
    var remainingCredits: Double = 0
    /// 基础套餐积分合计（月度订阅，月底清零）
    var remainingBaseCredits: Double = 0
    /// 平台奖励积分合计（一次性赠送包）
    var remainingRewardCredits: Double = 0
    /// 还没拉到余额的账号数。> 0 时合计是"已知部分的和"，界面要注明。
    var unknownCreditAccounts: Int = 0
    var accounts: [AccountInfo] = []
    var last7Days: [DayPoint] = []
    var fetchedAt: Date = .init()
    var reachable: Bool = false
}

/// 单个账号的余额。
///
/// **nil = 还没拉到，0 = 真的耗尽** —— 两者含义相反，合并成 0 会让面板把
/// "不知道"显示成"没额度了"。分桶的原因见网页面板 accounts-checkin-panel.tsx：
/// 基础套餐月底清零（轮换优先花它），平台奖励过期晚得多，合成一个数就丢了这层信息。
struct AccountInfo: Identifiable {
    var id: String { accountId }
    var accountId: String
    var nickname: String
    var credits: Double?          // 合计
    var baseCredits: Double?      // 基础套餐
    var rewardCredits: Double?    // 平台奖励
    var available: Bool
    var tokenValid: Bool
    var requestsSession: Int
    var creditsSpentSession: Double
}

struct DayPoint: Identifiable {
    var id: String { date }
    var date: String      // yyyy-MM-dd
    var tokens: Int
    var credits: Double
}

// MARK: - 网关返回的 JSON 结构

private struct UsageResponse: Decodable {
    struct Totals: Decodable {
        struct Bucket: Decodable {
            let requests: Int?
            let credits: Double?
            let tokens: Int?
        }
        let today: Bucket
        let d7: Bucket?
    }
    struct Day: Decodable {
        let date: String
        let requests: Int
        let credits: Double
        let tokens: Int
    }
    let totals: Totals
    let days: [Day]
}

private struct StatusResponse: Decodable {
    struct Account: Decodable {
        let account_id: String
        let nickname: String
        /// 可花总余额。可能为 null（未拉到）——绝不能当 0 处理
        let credits: Double?
        /// 基础套餐 / 平台奖励（2026-10-17 新增；旧版网关没有这两个字段）
        let base_credits: Double?
        let reward_credits: Double?
        let available: Bool
        let token_valid: Bool
        let requests_session: Int
        let credits_spent_session: Double
    }
    let accounts: [Account]
}

// MARK: - 客户端

enum GatewayClient {
    static let baseURL = URL(string: "http://127.0.0.1:8788")!

    /// 网关后台看板地址。
    static let dashboardURL = baseURL

    enum ClientError: Error, LocalizedError {
        case noKey
        case badStatus(Int)
        case decode(String)
        var errorDescription: String? {
            switch self {
            case .noKey: return "未找到 GATEWAY_API_KEY（检查项目 .env）"
            case .badStatus(let code): return "网关返回 HTTP \(code)"
            case .decode(let msg): return "响应解析失败：\(msg)"
            }
        }
    }

    private static func get(_ path: String, key: String) throws -> Data {
        var req = URLRequest(url: baseURL.appendingPathComponent(path))
        req.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        req.timeoutInterval = 8
        req.cachePolicy = .reloadIgnoringLocalCacheData

        let sem = DispatchSemaphore(value: 0)
        var result: Result<Data, Error>?
        URLSession.shared.dataTask(with: req) { data, resp, err in
            defer { sem.signal() }
            if let err = err { result = .failure(err); return }
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            guard code == 200, let data = data else {
                result = .failure(ClientError.badStatus(code)); return
            }
            result = .success(data)
        }.resume()

        if sem.wait(timeout: .now() + 10) == .timedOut {
            throw URLError(.timedOut)
        }
        switch result {
        case .success(let data): return data
        case .failure(let err): throw err
        case nil: throw URLError(.unknown)
        }
    }

    /// 拉取两路数据并合成快照。任一成功即可，失败保留调用方传进来的旧值。
    static func fetch(previous: GatewaySnapshot?) -> (snapshot: GatewaySnapshot, error: String?) {
        var snap = previous ?? GatewaySnapshot()
        var errors: [String] = []

        // reachable 表示"本次拉取是否成功"，必须每轮重置。
        // 旧实现直接从 previous 继承且末尾的 `if !reachable { reachable = false }` 是空操作，
        // 于是一次成功后，后续全失败也会一直显示"在线"（⚠️ 永远不出现）。
        snap.reachable = false

        guard let key = ConfigStore.gatewayAPIKey() else {
            return (snap, ClientError.noKey.localizedDescription)
        }

        // /admin/usage
        do {
            let data = try get("admin/usage", key: key)
            let usage = try JSONDecoder().decode(UsageResponse.self, from: data)
            snap.todayTokens = usage.totals.today.tokens ?? 0
            snap.todayCredits = usage.totals.today.credits ?? 0
            snap.todayRequests = usage.totals.today.requests ?? 0
            snap.last7Days = usage.days.suffix(7).map {
                DayPoint(date: $0.date, tokens: $0.tokens, credits: $0.credits)
            }
            snap.reachable = true
        } catch {
            errors.append(error.localizedDescription)
        }

        // /admin/status
        do {
            let data = try get("admin/status", key: key)
            let status = try JSONDecoder().decode(StatusResponse.self, from: data)
            snap.accounts = status.accounts.map {
                AccountInfo(
                    accountId: $0.account_id,
                    nickname: $0.nickname,
                    credits: $0.credits,
                    baseCredits: $0.base_credits,
                    rewardCredits: $0.reward_credits,
                    available: $0.available,
                    tokenValid: $0.token_valid,
                    requestsSession: $0.requests_session,
                    creditsSpentSession: $0.credits_spent_session
                )
            }
            // 合计只加已知余额：把 null 当 0 会把"未知"悄悄算成"没有"，
            // 于是网关刚启动、还没拉到积分的那几秒，面板会显示余额骤降。
            snap.remainingCredits = status.accounts.reduce(0) { $0 + ($1.credits ?? 0) }
            snap.remainingBaseCredits = status.accounts.reduce(0) { $0 + ($1.base_credits ?? 0) }
            snap.remainingRewardCredits = status.accounts.reduce(0) { $0 + ($1.reward_credits ?? 0) }
            snap.unknownCreditAccounts = status.accounts.filter { $0.credits == nil }.count
            snap.reachable = true
        } catch {
            errors.append(error.localizedDescription)
        }

        snap.fetchedAt = Date()
        return (snap, errors.isEmpty ? nil : errors.joined(separator: "；"))
    }
}
