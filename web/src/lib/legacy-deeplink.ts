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
 *                                            （usage 已于 2026-10-16 并入 overview，
 *                                              见下面的 ROUTE_ALIASES）
 *   ?facet=all|free                        → 带进 models 路由
 *                                            （旧书签里的 cn / global 会被忽略，退回「全部」）
 *   ?sort=family|cost|ctx|name            → 带进 models 路由
 *   ?q=<关键词>                            → 带进 models 路由
 *   ?w=d1|d7|d30|d90                      → 带进 overview 路由（用量区块的统计窗口）
 *   ?key=<API_KEY>                        → 存进 localStorage 后从地址栏抹掉
 *   ?theme=light|dark|system              → **不再生效**（面板固定浅色），
 *                                           但仍会从地址栏清掉，见下面说明
 *
 * 必须在渲染之前调用一次。
 */

export const ROUTES = ['overview', 'models', 'connect'] as const
export type RouteName = (typeof ROUTES)[number]

/**
 * 已合并/已废弃的旧路由 → 现在的路由。
 *
 * `usage` 于 2026-10-16 并入 `overview`。「用量」曾经是独立页面（也进过书签），
 * 所以这里**翻译**而不是报 404：地址栏里的 `#/usage` 会被改写成 `#/overview`，
 * 窗口参数 `w` 跟着一起走。
 */
const ROUTE_ALIASES: Record<string, RouteName> = {
  usage: 'overview',
}

/** 旧路由名 → 规范路由名。不是路由就返回 null。 */
function canonicalRoute(value: string | null): RouteName | null {
  if (value === null) return null
  if ((ROUTES as readonly string[]).includes(value)) return value as RouteName
  return ROUTE_ALIASES[value] ?? null
}

const DEFAULT_ROUTE: RouteName = 'overview'
const KEY_STORAGE = 'wbgw_key'

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

/** 旧格式的顶层查询参数，翻译完要一并抹掉 */
const LEGACY_PARAMS = ['tab', 'facet', 'sort', 'q', 'w', 'key', 'theme'] as const

/**
 * 读出地址栏 hash 里的路由。
 *
 * 不是合法路由（比如没有 hash、或者 `#/`）就返回 null，交给旧深链逻辑决定去向。
 * `rewritten` 表示读到的是**别名**（例如 `#/usage`），需要把 hash 改写成规范路由 ——
 * 返回规范名却把别名留在地址栏，会让"当前路由"和地址栏里的字面量长期不一致。
 */
function currentHashRoute(): { route: RouteName; rewritten: boolean } | null {
  const raw = window.location.hash.replace(/^#\/?/, '')
  const name = raw.split('?')[0].split('/')[0]
  const route = canonicalRoute(name)
  if (!route) return null
  return { route, rewritten: route !== name }
}

/** 只在地址栏真的需要变化时才 replaceState，避免平白多一条历史记录 */
function commit(url: URL): void {
  const next = `${url.pathname}${url.search}${url.hash}`
  const now = `${window.location.pathname}${window.location.search}${window.location.hash}`
  if (next !== now) window.history.replaceState({}, '', next)
}

export function translateLegacyDeepLink(): BootResult {
  const url = new URL(window.location.href)
  const params = url.searchParams

  absorbLegacyKey(params)

  // ⚠️ 这里曾经有个严重 bug：无条件按 ?tab=（缺省 overview）重算路由，
  // 再 replaceState 覆盖 hash。于是直接打开 `#/models` 会被立刻打回
  // `#/overview` —— 刷新、书签、分享链接全部失效，只有概览页能深链。
  //
  // 正确的语义：**只有当地址栏里没有新格式 hash 路由时，才需要翻译旧深链**。
  // 已经有合法 hash 就原样尊重它（连着它自己的 query 一起保留）。
  const existing = currentHashRoute()
  if (existing) {
    for (const name of LEGACY_PARAMS) params.delete(name)
    if (existing.rewritten) {
      // `#/usage?w=d90` → `#/overview?w=d90`：把 hash 自己的 query 原样带过去，
      // 不能顺手丢掉（那等于把用户选好的统计窗口静默换回默认）。
      const hashQuery = window.location.hash.split('?')[1] ?? ''
      url.hash = `#/${existing.route}${hashQuery ? `?${hashQuery}` : ''}`
    }
    commit(url)
    return { route: existing.route, hash: url.hash || `#/${existing.route}` }
  }

  const tab = params.get('tab')
  // ?tab= 可能给的是已废弃的路由名（usage），canonicalRoute 会把它折到现在的页面。
  const route: RouteName = canonicalRoute(tab) ?? DEFAULT_ROUTE

  // facet / sort / q 只对模型页有意义，w 只对概览的用量区块有意义；
  // 带错了路由会忽略，带对了就直接生效。
  const carried = new URLSearchParams()
  for (const name of ['facet', 'sort', 'q', 'w'] as const) {
    const value = params.get(name)
    if (value) carried.set(name, value)
  }

  // 清掉所有旧的顶层参数，避免地址栏里两套参数并存。
  // 'theme' 也留着清 —— 面板不再支持主题切换，但旧书签里的 ?theme=dark
  // 仍要抹掉，否则地址栏会一直挂着一个已经没人读的参数。
  for (const name of LEGACY_PARAMS) {
    params.delete(name)
  }

  const query = carried.toString()
  const hash = `#/${route}${query ? `?${query}` : ''}`

  url.hash = hash.slice(1)
  commit(url)

  return { route, hash }
}

/** 当前 hash 里带的查询参数（模型页用来读 facet/sort/q） */
export function readHashParams(): URLSearchParams {
  const hash = window.location.hash
  const index = hash.indexOf('?')
  return new URLSearchParams(index >= 0 ? hash.slice(index + 1) : '')
}
