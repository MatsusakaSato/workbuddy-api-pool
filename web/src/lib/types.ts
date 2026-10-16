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
  /** 'cn' | 'global' */
  region: string
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
  /** 余额。**可能为 null** —— 尚未拉到积分时为 null，不能当 0 处理 */
  credits: number | null
  requests_session: number
  credits_spent_session: number
}

/** GET /admin/status 的完整响应，也是 POST /admin/reload 的返回体 */
export interface StatusSnapshot {
  accounts: Account[]
  /** 各区域的模型目录大小，如 { cn: 34, global: 0 } */
  catalogs: Record<string, number>
  /** Unix 秒（浮点） */
  last_reload: number
  last_credit_refresh: number
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
 * 这是个**三态联合**，服务端会返回三种不同形状：
 *  · 不支持签到（国际版）：supported=false + note
 *  · 凭据不可用 / 上游报错：supported=true + error
 *  · 正常：supported=true + 下面那一堆字段
 * 所以除前四个字段外其余全是可选的，渲染时必须先判断。
 */
export interface CheckinAccount {
  account_id: string
  uin: string
  region: string
  supported: boolean
  /** 仅当 supported=false */
  note?: string
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
}

/** POST /admin/checkin/claim 的 results[] 元素 */
export interface ClaimResult {
  account_id: string
  ok: boolean
  /** ok=false 时的原因 */
  reason?: string
  /** ok=true 时上游返回的额外字段（如 credited），形状不固定 */
  [key: string]: unknown
}

export interface ClaimResponse {
  results: ClaimResult[]
}

/* ------------------------------------------------------------------ */
/* 模型                                                                */
/* ------------------------------------------------------------------ */

/** GET /admin/models 的 data[] 元素 */
export interface ModelEntry {
  id: string
  name: string
  /** 'cn' | 'global' */
  region: string
  context_window: number | null
  max_tokens: number | null
  /** 形如 "x0.00 credits"，是**字符串**不是数字 */
  credits: string | null
  cost: number | null
  owned_by: string
}

/** GET /v1/models 的 data[] 元素，比 /admin/models 少 name/region */
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
  /** 形如 "workbuddy-cn" 或 "workbuddy-cn (自检)" */
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
}

/** days[] 元素 —— 注意**没有** avg_ms / max_ms / error_rate */
export interface UsageDay {
  date: string
  requests: number
  errors: number
  credits: number
  tokens: number
}

export interface UsageModel extends UsageAggregate {
  model: string
}

export interface UsageAccount extends UsageAggregate {
  account: string
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
    d7: UsageAggregate
    d30: UsageAggregate
    all: UsageAggregate
  }
  /** 固定 30 天（含 0 值日），服务端保证连续性 */
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
  region?: string
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
