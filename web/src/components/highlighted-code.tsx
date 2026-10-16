/**
 * 语法高亮后的代码。
 *
 * 渲染成**真的 React 元素**（`<span style={{color}}>`）而不是
 * `dangerouslySetInnerHTML` —— shiki 的 `codeToHtml` 会给 HTML 字符串，
 * 用 React 元素渲染既不需要注入原始 HTML，也让「哪些东西进了 DOM」一目了然。
 *
 * 高亮未就绪时直接输出原文本，保证首帧就能读、且行数一致不跳布局。
 */

import { useHighlightedCode } from '@/hooks/use-highlighted-code'

export function HighlightedCode({ code, language }: { code: string; language?: string }) {
  const lines = useHighlightedCode(code, language)

  if (!lines) return <>{code}</>

  return (
    <>
      {lines.map((line, lineIndex) => (
        <span key={lineIndex}>
          {line.map((token, tokenIndex) => (
            <span key={tokenIndex} style={token.color ? { color: token.color } : undefined}>
              {token.content}
            </span>
          ))}
          {/* 行尾换行：<pre> 里缩进和换行都必须显式保留 */}
          {lineIndex < lines.length - 1 ? '\n' : null}
        </span>
      ))}
    </>
  )
}
