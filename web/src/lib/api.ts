/**
 * 网关 API 客户端。
 *
 * 保留了旧面板请求封装的语义（这些行为是被验证过的，不要改）：
 *  · Authorization: Bearer <页面注入的 key，注入失效时退回手工填的>
 *  · 连不上 → status 0 + "无法连接"（区别于 HTTP 错误，UI 要分开提示）
 *  · 错误信息优先取服务端的 OpenAI 风格信封 { error: { message } }
 *  · 拿不到 JSON 就退回原始文本片段
 *
 * 路径一律用相对地址：线上由网关同源提供，开发时由 Vite 的 server.proxy 转发，
 * 两种情况下浏览器看到的都是同源，所以**不需要 CORS**。
 */

import { resolveKey } from './key'

export class ApiError extends Error {
  readonly status: number
  readonly code?: string
  readonly type?: string

  constructor(message: string, status: number, code?: string, type?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.type = type
  }

  /** 连不上（区别于服务端返回了错误状态码） */
  get isOffline(): boolean {
    return this.status === 0
  }

  /** key 缺失或不正确 */
  get isAuthError(): boolean {
    return this.status === 401
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST'
  body?: unknown
  /** 额外的请求头。注意：**不要**给 /admin/selftest 加 X-Gateway-Test，
   *  服务端是故意给自检记账的（它真的消耗积分） */
  headers?: Record<string, string>
  signal?: AbortSignal
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, headers = {}, signal } = options

  const init: RequestInit = {
    method,
    signal,
    headers: {
      Authorization: `Bearer ${resolveKey()}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...headers,
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  }

  let response: Response
  try {
    response = await fetch(path, init)
  } catch (cause) {
    // AbortError 是正常的取消（切换路由等），原样抛出交给调用方忽略
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw new ApiError('无法连接网关，请确认服务在运行', 0)
  }

  const text = await response.text()

  let parsed: unknown
  try {
    parsed = text ? JSON.parse(text) : null
  } catch {
    parsed = null
  }

  if (!response.ok) {
    const envelope = parsed as { error?: { message?: string; code?: string; type?: string } } | null
    const message =
      envelope?.error?.message?.trim() ||
      text.slice(0, 200).trim() ||
      `HTTP ${response.status}`
    throw new ApiError(message, response.status, envelope?.error?.code, envelope?.error?.type)
  }

  if (parsed === null) {
    throw new ApiError('服务端返回的不是合法 JSON', response.status)
  }

  return parsed as T
}

/* ------------------------------------------------------------------ */
/* 具体端点                                                            */
/* ------------------------------------------------------------------ */

import type {
  CheckinOverview,
  ClaimResponse,
  Health,
  LogResponse,
  ModelEntry,
  SelftestResponse,
  Settings,
  StatusSnapshot,
  UsageResponse,
} from './types'

export const api = {
  health: () => request<Health>('/healthz'),

  status: () => request<StatusSnapshot>('/admin/status'),

  checkin: () => request<CheckinOverview>('/admin/checkin'),

  settings: () => request<Settings>('/admin/settings'),

  models: () => request<{ data: ModelEntry[] }>('/admin/models'),

  log: (limit = 80) => request<LogResponse>(`/admin/log?limit=${limit}`),

  usage: (days = 30) => request<UsageResponse>(`/admin/usage?days=${days}`),

  reload: () => request<StatusSnapshot>('/admin/reload', { method: 'POST' }),

  /**
   * 端到端自检：真发一条极短请求，**会消耗极少量积分**。
   *
   * 这里刻意不发 X-Gateway-Test 头 —— 服务端 _selftest() 里明确写了
   * 「自检也真的花了积分，所以要记账，否则本次消耗会少算」。
   * 它会被记进用量，并标记 upstream 为 "workbuddy-cn (自检)" 以便区分。
   */
  selftest: (model: string) =>
    request<SelftestResponse>('/admin/selftest', { method: 'POST', body: { model } }),

  /**
   * 领取签到。**半自动设计**：必须显式给出 account_ids，或用 all=true 明确表示领全部。
   * 服务端会拒绝空请求（400 confirmation_required）。
   */
  claimCheckin: (accountIds: string[]) =>
    request<ClaimResponse>('/admin/checkin/claim', {
      method: 'POST',
      body: { account_ids: accountIds },
    }),

  claimCheckinAll: () =>
    request<ClaimResponse>('/admin/checkin/claim', {
      method: 'POST',
      body: { all: true },
    }),
}
