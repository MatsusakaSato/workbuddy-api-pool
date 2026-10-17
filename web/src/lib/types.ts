/**
 * 网关接口的类型定义。
 *
 * 全部字段都是从**运行中的网关**实际响应里核对出来的，不是照代码猜的。
 * 对应服务端 gateway/server.py 的路由表与 pool.py / activity.py 的返回结构。
 */

/* ------------------------------------------------------------------ */
/* 账号                                                                */
/* ------------------------------------------------------------------ */

/** GET /admin/status 的 accounts[] 元素 */
export interface Account {
  account_id: string
  uin: string
  uid: string
  nickname: string
  domain: string
  source: string
  /** 形如 "2026-11-14 11:52:47" */
  expires_at: string
  refresh_expires_at: string
  token_valid: boolean
  refresh_valid: boolean
  available: boolean
  exhausted: boolean
  cooldown_remaining_s: number
  last_error: string
  /**
   * 可花总余额 = base_credits + reward_credits。
   * **可能为 null** —— 尚未拉到积分时为 null，不能当 0 处理（0 是耗尽）。
   *
   * 2026-10-17 之前这个字段读的是上游 `TotalDosage`，它把月度套餐的**池值**
   * 算进余额，于是「本周期已用光」的号也会显示还有 500+。现在口径已修正。
   */
  credits: number | null
  /** 与 credits 同值（更明确的别名）。新代码优先用它 */
  total_credits?: number | null
  /** 基础套餐积分（月度订阅，**月底清零**）。null = 未拉到 */
  base_credits?: number | null
  /** 平台奖励积分（签到/拉新等一次性赠送包）。null = 未拉到 */
  reward_credits?: number | null
  /** 基础积分的刷新时刻（本周期结束，Unix 秒） */
  base_refresh_at?: number | null
  /** 24 小时内会过期的奖励积分（提示用，不是扣减） */
  reward_expiring_soon?: number | null
  /** 那批奖励积分最先将过期的时刻（Unix 秒） */
  reward_expiring_soon_at?: number | null
  /** 上游 TotalDosage —— **仅用于对账**，不是余额 */
  total_dosage?: number | null
  /** 逐包明细，基础在前、先到期的在前 */
  packages?: CreditPackage[]
  /** 本次进程内的消耗速率（积分/小时）；没花过或样本太少时为 null */
  credits_per_hour?: number | null
  requests_session: number
  credits_spent_session: number
  credits_spent_total?: number
  requests_total?: number
  last_used_wall?: number
  in_flight?: number
}

/** 一个计费包（上游 Accounts[] 的一行，已归类） */
export interface CreditPackage {
  kind: 'base' | 'reward'
  name: string
  remain: number
  size: number
  /** Unix 秒；null = 不知道 */
  expires_at: number | null
}

/** GET /admin/status 的完整响应，也是 POST /admin/reload 的返回体 */
export interface StatusSnapshot {
  accounts: Account[]
  /** 模型目录大小（只有一个上游，所以是个数字而不是按区域分的对象） */
  catalog: number
  /** Unix 秒（浮点） */
  last_reload: number
  last_credit_refresh: number
  /**
   * 池级余额合计。**只累加已知账号** —— 一个还没拉到余额的号不该被当成 0 拉低合计，
   * 所以同时给出已知/未知账号数，让 "合计" 可以按需注明口径。
   * 旧版服务端没有这个字段（可选）。
   */
  credits?: {
    total: number
    base: number
    reward: number
    known_accounts: number
    unknown_accounts: number
  }
  session: {
    requests: number
    credits_spent: number
  }
}

/** GET /healthz */
export interface Health {
  ok: boolean
  accounts: number
}

/* ------------------------------------------------------------------ */
/* 签到                                                                */
/* ------------------------------------------------------------------ */

/**
 * GET /admin/checkin 的 accounts[] 元素。
 *
 * 服务端会返回两种形状：
 *  · 凭据不可用 / 上游报错：error
 *  · 正常：下面那一堆可选字段
 * 所以除 account_id / uin 外全是可选的，渲染时必须先判断。
 */
export interface CheckinAccount {
  account_id: string
  uin: string
  /** 仅当查询失败 */
  error?: string
  /** 以下仅当查询成功 */
  active?: boolean
  today_checked_in?: boolean
  streak_days?: number
  daily_credit?: number
  today_credit?: number
  total_credits?: number
  activity_name?: string
  theme_name?: string
  end_time?: string
  claim_button_text?: string
}

export interface CheckinOverview {
  accounts: CheckinAccount[]
  /**
   * 全自动签到的状态（2026-10-16 新增）。
   * 纯内存/落盘状态，服务端**不会**为它额外打上游请求，所以可以跟着面板一起轮询。
   * 旧版服务端不带这个字段，所以是可选的。
   */
  auto?: CheckinAutoStatus
}

/**
 * 自动签到在某个账号上的结论。
 *
 * action 的取值（服务端 gateway/pool.py 与 gateway/checkin.py 共同决定）：
 *  · claimed         本轮真的领到了（credited 有值）
 *  · claimed_manual  用户点「立即签到」领到的（credited 有值）
 *  · already         上游说今天已经领过
 *  · already_local   本地记录说今天已经领过（上游状态可能还没更新）
 *  · unavailable     凭据不可用，连查都没查成
 *  · inactive        签到活动未开始/已结束
 *  · unknown         上游没返回今日签到状态，不敢盲领
 *  · error           上游报错
 */
export interface CheckinAutoAccount {
  uin?: string
  nickname?: string
  /** 本地自然日，形如 2026-10-16 */
  date: string
  action: string
  /** 今天是否已经"不用再领了"（claimed / already / already_local 为 true） */
  ok: boolean
  /** 真正领到的积分；只有 claimed / claimed_manual 有值 */
  credited?: number | null
  error?: string
  /** unix 秒 */
  at: number
}

export interface CheckinAutoStatus {
  enabled: boolean
  interval_s: number
  jitter_s: number
  gap_s: number
  /** 服务端视角的本地自然日 */
  today: string
  /** unix 秒；0 表示还没跑过 */
  last_run_at: number
  last_run_age_s: number | null
  next_run_in_s: number | null
  /** 今天已经确认领到的账号数 */
  claimed_today: number
  last_summary: {
    total?: number
    claimed?: number
    settled?: number
    failed?: number
    reason?: string
    fatal?: string | null
    duration_s?: number
  }
  accounts: Record<string, CheckinAutoAccount>
}

/** POST /admin/checkin/claim 的 results[] 元素 */
export interface ClaimResult {
  account_id: string
  ok: boolean
  /** ok=false 时的原因 */
  reason?: string
  /** 本轮这个账号发生了什么（见 CheckinAutoAccount 的 action 说明） */
  action?: string
  uin?: string
  credited?: unknown
  /** ok=true 时上游返回的额外字段（如 credited），形状不固定 */
  [key: string]: unknown
}

export interface ClaimResponse {
  results: ClaimResult[]
  auto?: CheckinAutoStatus
}

/* ------------------------------------------------------------------ */
/* 模型                                                                */
/* ------------------------------------------------------------------ */

/** GET /admin/models 的 data[] 元素 */
export interface ModelEntry {
  id: string
  name: string
  context_window: number | null
  max_tokens: number | null
  /** 形如 "x0.00 credits"，是**字符串**不是数字 */
  credits: string | null
  cost: number | null
  owned_by: string
}

/** GET /v1/models 的 data[] 元素，比 /admin/models 少 name 与倍率等元数据 */
export interface ApiModel {
  id: string
  object?: string
  owned_by?: string
}

/* ------------------------------------------------------------------ */
/* 设置                                                                */
/* ------------------------------------------------------------------ */

/** GET /admin/settings —— **不含 API key** */
export interface Settings {
  version: string
  host: string
  port: number
  /** 形如 http://127.0.0.1:8788/v1 */
  base_url: string
  dashboard_url: string
  models_total: number
  accounts: {
    total: number
    available: number
    exhausted: number
    cooling: number
  }
  /** Gemini 独立上游（AI Studio 或本机 Antigravity 免费档） */
  gemini: {
    name: string
    base: string
    configured: boolean
  }
  limits: {
    max_rotations: number
    rate_limit_cooldown_s: number
    refresh_margin_h: number
    upstream_timeout_s: number
  }
  /** 自动签到的节奏（2026-10-16 新增，旧服务端不带） */
  checkin?: {
    auto_enabled: boolean
    interval_s: number
    jitter_s: number
    account_gap_s: number
  }
  paths: {
    state: string
    desktop_creds: string
    log: string
  }
}

/* ------------------------------------------------------------------ */
/* 请求日志                                                            */
/* ------------------------------------------------------------------ */

export interface LogItem {
  seq: number
  /** Unix 秒（浮点） */
  at: number
  /** 形如 "11:53:39" */
  time: string
  model: string
  account: string
  /** 形如 "workbuddy" 或 "workbuddy (自检)" */
  upstream: string
  stream: boolean
  latency_ms: number
  total_ms: number
  credit: number
  tokens: number
  ok: boolean
  error: string
  /** true = 回归测试流量（带 X-Gateway-Test 头），单独记账 */
  test: boolean
}

export interface LogStats {
  total_requests: number
  total_errors: number
  total_credits: number
  uptime_s: number
  /** 形如 "2026-10-16 11:51:36" */
  started_at: string
  persisted: boolean
  loaded_records: number
  test_requests: number
}

export interface LogResponse {
  stats: LogStats
  items: LogItem[]
}

/* ------------------------------------------------------------------ */
/* 用量                                                                */
/* ------------------------------------------------------------------ */

/** totals 与 models[]/accounts[] 共用的聚合结构 */
export interface UsageAggregate {
  requests: number
  errors: number
  /** 0–100 的百分数还是 0–1？服务端 _shape() 给出的是 0–1 的比例 */
  error_rate: number
  credits: number
  tokens: number
  avg_ms: number
  max_ms: number
  /**
   * 延迟分位数，由服务端的**定长直方图**估算（见 activity.py 的 LATENCY_BUCKETS）。
   *
   * 两个必须知道的性质：
   *  1. 它们是**桶上界**，所以只会偏大不会偏小（保守估计）；
   *  2. 精度受桶宽限制，低延迟段很准（±30%），高延迟段较粗。
   *
   * 用途只是画"分布条"的形状 —— 判断"慢是常态还是个别事故"。
   * 不要拿它当精确 SLA 数值展示。
   */
  p50_ms: number
  p90_ms: number
  p99_ms: number
}

/** days[] 元素 —— 注意**没有** avg_ms / max_ms / error_rate */
export interface UsageDay {
  date: string
  requests: number
  errors: number
  credits: number
  tokens: number
  /**
   * 当天的按模型拆分，已按 **tokens 降序**。用于鼠标悬停时回答
   * "这天是谁在跑、各吃了多少 token"。
   *
   * 注意：各项 tokens 之和**恒等于**本行的 tokens，requests 之和恒等于 requests
   * （服务端从同一份逐日聚合反推），所以可以直接当明细展示。
   * 按 tokens 而非请求数排序，是因为 hover 时最关心"谁最耗 token"。
   */
  models: { model: string; requests: number; tokens: number }[]
}

export interface UsageModel extends UsageAggregate {
  model: string
  /** 逐日请求数，长度与 days 严格相等且对齐（用于行内趋势线） */
  daily: number[]
}

export interface UsageAccount extends UsageAggregate {
  account: string
  /** 逐日请求数，长度与 days 严格相等且对齐（用于行内趋势线） */
  daily: number[]
}

export interface UsageResponse {
  generated_at: string
  retention_days: number
  persisted: boolean
  path: string
  loaded_records: number
  test_requests: number
  first_at: string
  last_at: string
  totals: {
    today: UsageAggregate
    /** 与 today 同口径（当天自然日），d1 是给窗口选择器用的编号键 */
    d1: UsageAggregate
    d7: UsageAggregate
    d30: UsageAggregate
    all: UsageAggregate
  }
  /** 含 0 值日的连续日期序列，长度由请求的 days 决定（服务端保证连续性） */
  days: UsageDay[]
  models: UsageModel[]
  accounts: UsageAccount[]
}

/* ------------------------------------------------------------------ */
/* 自检                                                                */
/* ------------------------------------------------------------------ */

export interface SelftestResponse {
  ok: boolean
  /** 失败时标明卡在哪一步：'pick' | 'upstream' */
  stage?: string
  model?: string
  account?: string
  /** 成功时上游的一句话回复 */
  reply?: string
  latency_ms?: number
  tokens?: number
  credit?: number | null
  error?: string
  kind?: string
}

/* ------------------------------------------------------------------ */
/* 错误                                                                */
/* ------------------------------------------------------------------ */

/** 服务端统一的 OpenAI 风格错误信封 */
export interface ErrorEnvelope {
  error: {
    message: string
    type: string
    code: string
  }
}
