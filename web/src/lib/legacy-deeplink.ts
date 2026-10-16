/**
 * 旧深链翻译层。
 *
 * 旧面板用的是查询串（`?tab=models&facet=free&sort=cost&q=abc`），新面板用 HashRouter
 * （`#/models?facet=free&sort=cost&q=abc`）。为了让**已有的书签和文档链接继续可用**，
 * 启动时把旧的查询串翻译成 hash，然后把旧参数从地址栏清掉。
 *
 * 这是有意的债务清理：不引入服务端的 SPA 回退（那要改 server.py），
 * 同时又不在 URL 里长期留两套参数。
 *
 * 兼容的旧参数：
 *   ?tab=overview|models|usage|connect    → #/<name>
 *   ?facet=all|free|cn|global             → 带进 models 路由
 *   ?sort=family|cost|ctx|name            → 带进 models 路由
 *   ?q=<关键词>                            → 带进 models 路由
 *   ?key=<API_KEY>                        → 存进 localStorage 后从地址栏抹掉
 *   ?theme=light|dark|system              → **不再生效**（面板固定浅色），
 *                                           但仍会从地址栏清掉，见下面说明
 *
 * 必须在渲染之前调用一次。
 */

export const ROUTES = ['overview', 'models', 'usage', 'connect'] as const
export type RouteName = (typeof ROUTES)[number]

const DEFAULT_ROUTE: RouteName = 'overview'
const KEY_STORAGE = 'wbgw_key'

function isRouteName(value: string | null): value is RouteName {
  return value !== null && (ROUTES as readonly string[]).includes(value)
}

/**
 * 把旧的 `?key=` 深链转成 localStorage，并立即从地址栏清除。
 *
 * 为什么要立刻清除：key 留在地址栏里会被记进浏览器历史、也可能出现在
 * 用户随手分享的截图/链接里。这是旧面板本来就有的行为，保留它。
 */
function absorbLegacyKey(params: URLSearchParams): void {
  const key = params.get('key')
  if (!key) return
  try {
    localStorage.setItem(KEY_STORAGE, key.trim())
  } catch {
    /* 忽略 */
  }
  params.delete('key')
}

export interface BootResult {
  /** 应该跳到哪个路由 */
  route: RouteName
  /** 翻译后写进 hash 的完整串（含 query），用于 history.replaceState */
  hash: string
}

export function translateLegacyDeepLink(): BootResult {
  const url = new URL(window.location.href)
  const params = url.searchParams

  absorbLegacyKey(params)

  const tab = params.get('tab')
  const route: RouteName = isRouteName(tab) ? tab : DEFAULT_ROUTE

  // facet / sort / q 只对模型页有意义；其它页带上也没坏处，路由会忽略
  const carried = new URLSearchParams()
  for (const name of ['facet', 'sort', 'q'] as const) {
    const value = params.get(name)
    if (value) carried.set(name, value)
  }

  // 清掉所有旧的顶层参数，避免地址栏里两套参数并存。
  // 'theme' 也留着清 —— 面板不再支持主题切换，但旧书签里的 ?theme=dark
  // 仍要抹掉，否则地址栏会一直挂着一个已经没人读的参数。
  for (const name of ['tab', 'facet', 'sort', 'q', 'key', 'theme'] as const) {
    params.delete(name)
  }

  const query = carried.toString()
  const hash = `#/${route}${query ? `?${query}` : ''}`

  // 只在确实需要时才动历史记录，避免平白多一条 history 条目
  url.hash = hash.slice(1)
  const next = `${url.pathname}${url.search}${hash}`
  if (next !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
    window.history.replaceState({}, '', next)
  }

  return { route, hash }
}

/** 当前 hash 里带的查询参数（模型页用来读 facet/sort/q） */
export function readHashParams(): URLSearchParams {
  const hash = window.location.hash
  const index = hash.indexOf('?')
  return new URLSearchParams(index >= 0 ? hash.slice(index + 1) : '')
}
