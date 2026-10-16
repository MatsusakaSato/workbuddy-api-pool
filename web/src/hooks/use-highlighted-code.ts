/**
 * 懒加载语法高亮。
 *
 * 返回 `null` 表示「还没有结果」—— 调用方应当**先渲染纯文本**，
 * 等 token 到了再换成高亮版。理由：
 *   · 高亮是纯装饰，为它显示骨架 / 转圈是本末倒置（代码本来就能读）；
 *   · 分两帧渲染不会改变行数，所以不会引起布局跳动。
 * 语言无法识别时返回 `null` 并保持 `null`，即永久纯文本。
 */

import { useEffect, useState } from 'react'
import { highlight, resolveLang, type HighlightToken } from '@/lib/highlighter'

export function useHighlightedCode(
  code: string,
  language: string | undefined,
): HighlightToken[][] | null {
  const [lines, setLines] = useState<HighlightToken[][] | null>(null)

  useEffect(() => {
    // 语言不认识就直接放弃，连 shiki 都不加载
    if (!resolveLang(language)) {
      setLines(null)
      return
    }

    let alive = true
    highlight(code, language)
      .then((result) => {
        // 组件已卸载或 code 变了就丢弃这次结果，避免旧结果覆盖新的
        if (alive) setLines(result)
      })
      .catch(() => {
        // 高亮失败不该让整个页面崩 —— 静默退回纯文本
        if (alive) setLines(null)
      })

    return () => {
      alive = false
    }
  }, [code, language])

  return lines
}
