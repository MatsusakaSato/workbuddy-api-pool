/**
 * 展示层格式化。
 *
 * 这里的每一个函数都直接影响"专业感"，尤其是数值精度与等宽数字的配合。
 * 规矩：
 *  · 所有数值列渲染时都要加 `tnum` 类（见 index.css），否则轮询刷新时宽度会跳
 *  · 余额/积分保留到有意义的最小位数，不要一律 toFixed(2) 把 1831 显示成 1831.00
 *  · 时间一律用本地时区，且服务端返回的 "YYYY-MM-DD HH:mm:ss" 必须手工解析
 *    （不要交给 new Date(string)，Safari 会解析失败得到 Invalid Date）
 */

/** 服务端的时间格式：本地时区、无时区标记 */
const NAIVE_DATETIME = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$/

/** 解析 "2026-11-14 11:52:47"。失败返回 null，绝不返回 Invalid Date。 */
export function parseServerDate(value: string | null | undefined): Date | null {
  if (!value) return null
  const m = NAIVE_DATETIME.exec(value.trim())
  if (!m) {
    const fallback = new Date(value)
    return Number.isNaN(fallback.getTime()) ? null : fallback
  }
  const [, y, mo, d, h, mi, s] = m
  return new Date(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s))
}

/** 从 Unix 秒（浮点，服务端快照用的格式）转 Date */
export function fromUnixSeconds(seconds: number | null | undefined): Date | null {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds <= 0) return null
  return new Date(seconds * 1000)
}

/* ------------------------------------------------------------------ */
/* 数值                                                                */
/* ------------------------------------------------------------------ */

const intFormatter = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 })

/** 1234 → "1,234" */
export function formatInt(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return intFormatter.format(Math.round(value))
}

/**
 * 积分/额度。刻意做成自适应精度：
 * 0.05 → "0.05"、1831 → "1,831"、1200.5 → "1,200.5"
 * 不要用固定小数位 —— 会把整数积分显示成 "1,831.00"，很难看且白占宽度。
 */
export function formatCredits(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  if (value === 0) return '0'
  const abs = Math.abs(value)
  // 小于 1 的保留 2 位（0.05），小于 100 的保留 1 位（12.5），其余取整
  const digits = abs < 1 ? 2 : abs < 100 ? 1 : 0
  const fixed = Number(value.toFixed(digits))
  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  }).format(fixed)
}

/**
 * 余额卡上的数字。与 `formatCredits` 的区别只有一个：**保留 1 位小数**（四舍五入）。
 *
 * 为什么余额要显示小数：上游给的是 `831.53000015` 这样的精确值，而「基础 + 奖励」
 * 只有在两个数都保留小数时才加得回来。若都向下取整，两个桶各丢 0.5，
 * 用户拿合计去对客户端就会差 1 分。而 `formatCredits` 对 >=100 的数直接取整，
 * 那正是过去「显示不准确」给人的观感之一。
 *
 * 仍然是自适应：整数 → 不带小数点（"500"），有零头 → "831.5"。
 */
export function formatBalance(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const rounded = Math.round(value * 10) / 10
  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 1,
  }).format(rounded)
}

/** 紧凑数值，给图表坐标轴用：1234 → "1.2k" */
export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1)}M`
  if (abs >= 1_000) return `${(value / 1_000).toFixed(abs >= 10_000 ? 0 : 1)}k`
  return String(Math.round(value))
}

/** 上下文窗口：960000 → "960K"、128000 → "128K"、1048576 → "1M" */
export function formatTokens(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  if (value >= 1_000_000) {
    const m = value / 1_000_000
    return `${m % 1 === 0 ? m : m.toFixed(1)}M`
  }
  if (value >= 1_000) {
    const k = value / 1_000
    return `${k % 1 === 0 ? k : k.toFixed(1)}K`
  }
  return String(value)
}

/* ------------------------------------------------------------------ */
/* 时间                                                                */
/* ------------------------------------------------------------------ */

/** 毫秒 → "234ms" / "1.5s" / "12.3s" */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  const s = ms / 1000
  if (s < 10) return `${s.toFixed(1)}s`
  if (s < 60) return `${Math.round(s)}s`
  return `${Math.floor(s / 60)}m${Math.round(s % 60)}s`
}

/** 秒 → "28m" / "3h12m" / "2d4h" */
export function formatUptime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) {
    return '—'
  }
  const s = Math.floor(seconds)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h${m % 60}m`
  return `${Math.floor(h / 24)}d${h % 24}h`
}

/** 冷却剩余秒数 → "8m20s"；0 → null（表示没在冷却） */
export function formatCooldown(seconds: number | null | undefined): string | null {
  if (!seconds || seconds <= 0) return null
  return formatUptime(seconds)
}

/** "HH:mm:ss"，用于日志时间列 */
export function formatClock(date: Date | null): string {
  if (!date) return '—'
  return date.toLocaleTimeString('zh-CN', { hour12: false })
}

/** "MM-DD HH:mm"，用于时间戳 */
export function formatDateTime(date: Date | null): string {
  if (!date) return '—'
  const mm = String(date.getMonth() + 1).padStart(2, '0')
  const dd = String(date.getDate()).padStart(2, '0')
  const hh = String(date.getHours()).padStart(2, '0')
  const mi = String(date.getMinutes()).padStart(2, '0')
  return `${mm}-${dd} ${hh}:${mi}`
}

/** 相对时间："刚刚" / "3 分钟前" / "2 小时前" / "5 天前" */
export function formatRelative(date: Date | null, now = Date.now()): string {
  if (!date) return '—'
  const diff = Math.floor((now - date.getTime()) / 1000)
  if (diff < 0) return '刚刚'
  if (diff < 10) return '刚刚'
  if (diff < 60) return `${diff} 秒前`
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  return `${Math.floor(diff / 86400)} 天前`
}

/** "2026-11-14 11:52:47" → 距离现在还有几天（可为负 = 已过期） */
export function daysUntil(value: string | null | undefined, now = Date.now()): number | null {
  const date = parseServerDate(value)
  if (!date) return null
  return Math.round((date.getTime() - now) / 86_400_000)
}

/** 百分比。服务端的 error_rate 是 0–1 的比例。 */
export function formatPercent(rate: number | null | undefined, digits = 1): string {
  if (rate === null || rate === undefined || !Number.isFinite(rate)) return '—'
  return `${(rate * 100).toFixed(digits)}%`
}

/* ------------------------------------------------------------------ */
/* 模型                                                                */
/* ------------------------------------------------------------------ */

/**
 * 模型的 credits 字段是形如 "x0.00 credits" 的**字符串**。
 * 这里抽成 "x0.00"，因为后面已经单独有"倍率"这一列标题，
 * 每行再重复一遍 "credits" 是噪音。
 */
export function formatCreditMultiplier(raw: string | null | undefined): string {
  if (!raw) return '—'
  const m = /(x\s*[\d.]+)/i.exec(raw)
  return m ? m[1].replace(/\s+/g, '') : raw
}

/**
 * 模型家族：把 id 里的版本/后缀剥掉，用于分组。
 * 例：deepseek-v4-flash / deepseek-v4-pro → "deepseek-v4"
 *     claude-sonnet-4-20250514 → "claude-sonnet-4"
 *     未识别时退回 id 本身，保证一定有个可用的分组键。
 */
export function modelFamily(id: string): string {
  // 去掉日期后缀（-20250514 / -2024-05-14）
  const noDate = id.replace(/[-_]?\d{8}$/, '').replace(/[-_]?\d{4}-\d{2}-\d{2}$/, '')
  // 去掉尾部的档位词，归到同一家族
  const noTier = noDate.replace(
    /[-_](flash|pro|plus|max|mini|mini-high|air|lite|turbo|preview|latest|high|low|thinking)$/i,
    '',
  )
  return noTier || id
}

/* ------------------------------------------------------------------ */
/* 账号状态判定                                                        */
/* ------------------------------------------------------------------ */

export type AccountTone = 'success' | 'warning' | 'danger' | 'idle'

export interface AccountState {
  tone: AccountTone
  label: string
  /** 给 tooltip 用的补充说明 */
  detail?: string
}

/**
 * 把账号的一堆布尔/数字压成**一个**状态。
 * 顺序很重要：先判过期，再判冷却，再判耗尽，最后才是可用 ——
 * 否则一个既过期又冷却的账号会显示成"冷却中"，掩盖更严重的问题。
 */
export function accountState(account: {
  available: boolean
  exhausted: boolean
  cooldown_remaining_s: number
  token_valid: boolean
  refresh_valid: boolean
  last_error: string
}): AccountState {
  if (!account.token_valid && !account.refresh_valid) {
    return { tone: 'danger', label: '凭据失效', detail: 'access 与 refresh token 都不可用，需重新登录客户端' }
  }
  if (!account.token_valid) {
    return { tone: 'danger', label: '待续期', detail: 'access token 已过期，但仍可用 refresh token 续期' }
  }
  if (account.exhausted) {
    return { tone: 'warning', label: '额度耗尽', detail: '积分已用尽，需签到或更换账号' }
  }
  const cooldown = formatCooldown(account.cooldown_remaining_s)
  if (cooldown) {
    return { tone: 'warning', label: `冷却中 ${cooldown}`, detail: account.last_error || '被上游限流，冷却结束后自动恢复' }
  }
  if (!account.available) {
    return { tone: 'idle', label: '不可用', detail: account.last_error || undefined }
  }
  return { tone: 'success', label: '可用' }
}
