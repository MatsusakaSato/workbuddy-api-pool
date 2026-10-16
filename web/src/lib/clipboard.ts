/**
 * 剪贴板。
 *
 * 为什么不能只用 navigator.clipboard：
 * 它要求**安全上下文**。http://127.0.0.1 和 http://localhost 在 Chrome/Firefox 里
 * 算安全上下文，但这个面板完全可能被用局域网 IP 打开（比如从另一台机器看），
 * 那时 navigator.clipboard 直接是 undefined。所以 execCommand 回退不是可选项。
 *
 * （execCommand 已废弃，但它是唯一在所有浏览器下都能用的同步回退路径，
 *   且只在没有 Clipboard API 时才走到。）
 */

export async function copyText(text: string): Promise<boolean> {
  if (!text) return false

  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      /* 用户拒绝授权或其它异常 → 走回退 */
    }
  }

  try {
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    // 必须留在文档流里才能选中；用 fixed + 移出视口而不是 display:none
    area.style.cssText = 'position:fixed;top:-1000px;left:0;opacity:0;pointer-events:none'
    document.body.appendChild(area)
    area.select()
    area.setSelectionRange(0, text.length)
    const ok = document.execCommand('copy')
    area.remove()
    return ok
  } catch {
    return false
  }
}
