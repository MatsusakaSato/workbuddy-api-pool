/**
 * API key 的来源与存储。
 *
 * 优先顺序（沿用旧面板的行为，只是把来源讲清楚）：
 *   1. 页面注入 —— 服务端 `_dashboard()` 把 "__GATEWAY_KEY__" 换成真实 key。
 *      这是正常路径，用户打开就能用，什么都不用配。
 *   2. localStorage['wbgw_key'] —— 手工填的，或 `?key=` 深链写进来的。
 *      注入失效时（比如直接打开 HTML 文件、或改了服务端配置）的兜底。
 *
 * 为什么还要留手工填：面板是拿来排障的。如果注入的 key 和服务端当前的 key
 * 对不上（比如刚改过 .env 还没重启浏览器），得有办法现场填一个，
 * 而不是让人去翻 DevTools。
 */

const KEY_STORAGE = 'wbgw_key'
const PLACEHOLDER = '__GATEWAY_KEY__'

declare global {
  interface Window {
    __DSH_GATEWAY_KEY__?: string
  }
}

/** 页面注入的 key；占位符原样存在或为空都算"没有" */
export function injectedKey(): string {
  const raw = window.__DSH_GATEWAY_KEY__
  if (!raw || raw === PLACEHOLDER) return ''
  return raw.trim()
}

export function storedKey(): string {
  try {
    return (localStorage.getItem(KEY_STORAGE) ?? '').trim()
  } catch {
    return ''
  }
}

/** 当前生效的 key：注入优先，其次 localStorage */
export function resolveKey(): string {
  return injectedKey() || storedKey()
}

export function hasKey(): boolean {
  return resolveKey().length > 0
}

export function saveKey(key: string): void {
  try {
    const trimmed = key.trim()
    if (trimmed) localStorage.setItem(KEY_STORAGE, trimmed)
    else localStorage.removeItem(KEY_STORAGE)
  } catch {
    /* 隐私模式下写入会抛错，忽略 */
  }
}

export function clearKey(): void {
  try {
    localStorage.removeItem(KEY_STORAGE)
  } catch {
    /* 忽略 */
  }
}

/** 只显示头尾，用于界面提示。永远不要整串渲染出来。 */
export function maskKey(key: string): string {
  if (!key) return ''
  if (key.length <= 12) return `${key.slice(0, 3)}…`
  return `${key.slice(0, 8)}…${key.slice(-4)}`
}
