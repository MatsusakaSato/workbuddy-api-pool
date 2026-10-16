/**
 * 离线语法高亮（shiki）。
 *
 * 两个刻意的决定：
 *
 * **1. 只懒加载，且只有「接入」页用。**
 * shiki 核心 + 3 个语法压缩后几十 KB，而全站只有接入页要展示代码。
 * 所以走动态 `import()`，概览 / 模型 / 用量三页一个字节都不加载。
 *
 * **2. 主题里的颜色写的是 `var(--tok-*)`，不是十六进制值。**
 * shiki 只是把 theme 里的颜色字符串原样写进 inline style，不做校验也不做归一化
 * （已实测：`var(--tok-kw)` 会被原样保留）。这么做的好处：
 *   · `index.css` 里的 `--tok-*` 是唯一真实来源，不会和 TS 里的色表各写一份慢慢写飘
 *     —— 改配色只需要动 CSS；
 *   · 顺带的好处：万一以后要恢复深色主题，只要在 CSS 里给 `.dark` 覆盖
 *     `--tok-*` 即可，**这份 TS 一行都不用改**，也不需要官方双主题那套
 *     `--shiki-light` / `--shiki-dark` 的胶水代码。
 *
 * （面板目前固定浅色，不做主题切换。见 index.css 第 3 节的说明。）
 *
 * 引擎用 JavaScript 正则版（`createJavaScriptRegexEngine`），**不加载 WASM** ——
 * 保持整个面板「断网也能开」且不需要额外的 .wasm 静态资源。
 */

import { createHighlighterCore, type HighlighterCore } from 'shiki/core'
import { createJavaScriptRegexEngine } from 'shiki/engine/javascript'

/**
 * token 配色主题。
 *
 * 作用域按 TextMate 命名，从宽到窄排列 —— shiki 取**最后一个**匹配的作用域，
 * 所以越具体的规则要放越后面（例如先把 `keyword` 全涂蓝，
 * 再让 `keyword.operator.new` 走函数色覆盖它）。
 */
const TOKEN_THEME = {
  name: 'wbgw-tokens',
  type: 'light' as const,
  colors: {
    // 没有命中任何作用域的 token 用它 —— 直接接全局前景色，深浅自动跟随
    'editor.foreground': 'var(--foreground)',
  },
  settings: [
    {
      scope: ['comment', 'punctuation.definition.comment'],
      settings: { foreground: 'var(--tok-com)' },
    },
    {
      scope: ['string', 'punctuation.definition.string', 'string.quoted'],
      settings: { foreground: 'var(--tok-str)' },
    },
    {
      scope: ['constant.numeric', 'constant.language', 'constant.character'],
      settings: { foreground: 'var(--tok-num)' },
    },
    {
      scope: ['keyword', 'storage.type', 'storage.modifier', 'storage.type.function'],
      settings: { foreground: 'var(--tok-kw)' },
    },
    {
      scope: [
        'entity.name.function',
        'support.function',
        'variable.function',
        'meta.function-call',
      ],
      settings: { foreground: 'var(--tok-fn)' },
    },
    {
      scope: [
        'punctuation',
        'punctuation.separator',
        'punctuation.terminator',
        'punctuation.definition',
      ],
      settings: { foreground: 'var(--tok-punct)' },
    },
  ],
}

/**
 * 片段里的 `language` 标签 → shiki 的语法 id。
 * 对不上的一律返回 null（走纯文本），**不要**猜一个相近的语言 ——
 * 用错语法比不高亮更糟（会把正确的代码标错颜色）。
 *
 * 注意这里**故意没有 typescript**：本项目的片段里没有类型标注，
 * 用 javascript 语法的结果一样，而 typescript 语法包大 177 KB。
 * 等真的出现类型标注再说 —— 那时也应该给 Snippet 传 highlightAs。
 */
const LANG_ALIAS: Record<string, string> = {
  sh: 'shell',
  bash: 'shell',
  shell: 'shell',
  zsh: 'shell',
  py: 'python',
  python: 'python',
  js: 'javascript',
  javascript: 'javascript',
  // 给用户看的文件类型提示是 "ts"，但语法按 javascript 走（片段里无类型标注）
  ts: 'javascript',
}

export function resolveLang(language: string | undefined): string | null {
  if (!language) return null
  return LANG_ALIAS[language.trim().toLowerCase()] ?? null
}

let pending: Promise<HighlighterCore> | null = null

/**
 * 加载（并缓存）highlighter。
 * 模块级 Promise 缓存：多个代码块同时挂载时只会真正初始化一次。
 */
export function loadHighlighter(): Promise<HighlighterCore> {
  pending ??= createHighlighterCore({
    themes: [TOKEN_THEME],
    langs: [
      import('shiki/langs/shell.mjs'),
      import('shiki/langs/python.mjs'),
      import('shiki/langs/javascript.mjs'),
    ],
    engine: createJavaScriptRegexEngine(),
  })
  return pending
}

export interface HighlightToken {
  content: string
  color?: string
}

/** 把代码切成「行 → token」，颜色为 CSS 变量引用 */
export async function highlight(
  code: string,
  language: string | undefined,
): Promise<HighlightToken[][] | null> {
  const lang = resolveLang(language)
  if (!lang) return null

  const highlighter = await loadHighlighter()
  const { tokens } = highlighter.codeToTokens(code, { lang, theme: TOKEN_THEME.name })
  return tokens.map((line) => line.map((t) => ({ content: t.content, color: t.color })))
}
