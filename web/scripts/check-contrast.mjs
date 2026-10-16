/**
 * 对比度检查器
 *
 * 直接读 src/index.css 里**实际声明**的 token 值，重算 WCAG 对比度并断言。
 * 关键点：它校验的是真正写进样式的值，不是一张可能和样式漂移的副本 ——
 * 所以任何人改了颜色，跑一下这个脚本就知道有没有踩线。
 *
 * 用法：node scripts/check-contrast.mjs [--verbose]
 * 退出码非 0 = 有 must 级别的项不达标。
 */
import fs from 'node:fs'
import path from 'node:path'

const CSS = path.resolve(import.meta.dirname, '../src/index.css')

/* ---------- 解析 CSS 自定义属性 ---------- */

/** 从 css 文本里取出某个选择器块内、**顶层**（不含 @supports/@media 内层）的声明 */
function readBlock(css, selector) {
  const start = css.indexOf(selector + ' {')
  if (start < 0) throw new Error(`找不到选择器块: ${selector}`)
  const open = css.indexOf('{', start)
  let depth = 0
  let end = open
  for (let i = open; i < css.length; i++) {
    if (css[i] === '{') depth++
    else if (css[i] === '}') {
      depth--
      if (depth === 0) { end = i; break }
    }
  }
  const body = css.slice(open + 1, end)
  const out = {}
  // 按行扫描，只收 depth 为 0 的行（跳过 @supports 内层的 P3 值）
  let d = 0
  for (const line of body.split('\n')) {
    const decl = d === 0 && line.match(/^\s*(--[a-z0-9-]+)\s*:\s*([^;]+);/i)
    if (decl) out[decl[1]] = decl[2].trim()
    d += (line.match(/\{/g) || []).length
    d -= (line.match(/\}/g) || []).length
  }
  return out
}

/** 解析 var() 链，返回最终字面量 */
function resolve(tokens, value, seen = new Set()) {
  const m = value.match(/^var\(\s*(--[a-z0-9-]+)\s*\)$/i)
  if (!m) return value
  const key = m[1]
  if (seen.has(key)) throw new Error(`var() 循环引用: ${key}`)
  if (!(key in tokens)) throw new Error(`token 未定义: ${key}`)
  seen.add(key)
  return resolve(tokens, tokens[key], seen)
}

/* ---------- WCAG 对比度 ---------- */

function toRgb(value) {
  const hex = value.trim()
  const m3 = hex.match(/^#([0-9a-f]{3})$/i)
  const m6 = hex.match(/^#([0-9a-f]{6})$/i)
  if (m6) {
    return [1, 3, 5].map((i) => parseInt(m6[1].slice(i - 1, i + 1), 16))
  }
  if (m3) {
    return m3[1].split('').map((c) => parseInt(c + c, 16))
  }
  throw new Error(`无法解析颜色（P3 或命名色需人工确认）: ${value}`)
}

const lin = (c) => {
  const n = c / 255
  return n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4
}
function luminance(value) {
  const [r, g, b] = toRgb(value).map(lin)
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

/* ---------- 待校验的配对清单 ----------
   level:
     'text'    正文，需 4.5:1
     'large'   大号文字（≥18.66px 粗体或 ≥24px），需 3:1
     'nontext' 图形对象/状态点，需 3:1
     'info'    仅报告，不作断言（例如输入框边框这种全行业都达不到的项）
   min: 覆盖该 level 的默认门槛
*/
const PAIRS = [
  // —— 中性文字 ——
  ['text', '页面正文字色', 'foreground', 'background'],
  ['text', '页面正文字色/卡片', 'foreground', 'card'],
  ['text', '次要文字', 'muted-foreground', 'background'],
  ['text', '次要文字/卡片', 'muted-foreground', 'card'],
  ['text', '次要文字/浅底片', 'muted-foreground', 'muted'],
  ['text', '次要文字/侧栏', 'sidebar-foreground', 'sidebar'],
  ['text', '侧栏次要文字', 'muted-foreground', 'sidebar'],

  // —— 状态文字 on 页面底 ——
  ['text', '成功文字', 'success', 'background'],
  ['text', '成功文字/卡片', 'success', 'card'],
  ['text', '警告文字', 'warning', 'background'],
  ['text', '警告文字/卡片', 'warning', 'card'],
  ['text', '危险文字', 'danger', 'background'],
  ['text', '危险文字/卡片', 'danger', 'card'],
  ['text', '信息文字', 'info', 'background'],
  ['text', '信息文字/卡片', 'info', 'card'],
  ['text', '空闲文字', 'idle', 'background'],
  ['text', '空闲文字/卡片', 'idle', 'card'],

  // —— 状态文字 on 浅色底片（这个组合最容易被忽略） ——
  ['text', '成功文字/成功底片', 'success', 'success-subtle'],
  ['text', '警告文字/警告底片', 'warning', 'warning-subtle'],
  ['text', '危险文字/危险底片', 'danger', 'danger-subtle'],
  ['text', '信息文字/信息底片', 'info', 'info-subtle'],
  ['text', '空闲文字/空闲底片', 'idle', 'idle-subtle'],

  // —— 实心块上的文字 ——
  ['text', '成功实心/前景', 'success-foreground', 'success-solid'],
  ['text', '警告实心/前景', 'warning-foreground', 'warning-solid'],
  ['text', '危险实心/前景', 'danger-foreground', 'danger-solid'],
  ['text', '信息实心/前景', 'info-foreground', 'info-solid'],
  ['text', '主按钮', 'primary-foreground', 'primary'],

  // —— 非文本：状态点 ——
  ['nontext', '成功状态点', 'success-dot', 'background'],
  ['nontext', '成功状态点/卡片', 'success-dot', 'card'],
  ['nontext', '警告状态点', 'warning-dot', 'background'],
  ['nontext', '警告状态点/卡片', 'warning-dot', 'card'],
  ['nontext', '危险状态点', 'danger-dot', 'background'],
  ['nontext', '危险状态点/卡片', 'danger-dot', 'card'],
  ['nontext', '信息状态点', 'info-dot', 'background'],
  ['nontext', '信息状态点/卡片', 'info-dot', 'card'],
  ['nontext', '空闲状态点', 'idle-dot', 'background'],
  ['nontext', '空闲状态点/卡片', 'idle-dot', 'card'],

  // —— 非文本：图表序列（必须有足够区分度） ——
  ['nontext', '图表 1', 'chart-1', 'card'],
  ['nontext', '图表 2', 'chart-2', 'card'],
  ['nontext', '图表 3', 'chart-3', 'card'],
  ['nontext', '图表 4', 'chart-4', 'card'],
  ['nontext', '图表 5', 'chart-5', 'card'],

  // —— 非文本：交互组件外框（受 WCAG 1.4.11 约束，必须 ≥3:1） ——
  ['nontext', '输入框边框/卡片', 'input', 'card'],
  ['nontext', '输入框边框/页面底', 'input', 'background'],
  // 焦点环是可感知性的兜底，必须实测（WCAG 2.4.11 / 1.4.11）
  ['nontext', '焦点环/页面底', 'ring', 'background'],
  ['nontext', '焦点环/卡片', 'ring', 'card'],

  // —— 仅报告：结构性边框 ——
  // 卡片边、分隔线是**装饰性**的，不构成"识别交互组件"的必要视觉信息，
  // 因此不在 WCAG 1.4.11 的适用范围内（该条款针对 user interface components）。
  // 这里只测量、不断言 —— 强行做到 3:1 需要约 #767676 的灰，
  // 会让整个界面变成 1990 年代的表单外观，代价远大于收益。
  // 交互状态的可感知性由焦点环（实测 ≥3:1）与 hover 底色变化来保证。
  ['info', '组件边框/页面底', 'border', 'background'],
  ['info', '组件边框/卡片', 'border', 'card'],
  ['info', '表格行分隔线', 'border-subtle', 'card'],
]

const THRESHOLD = { text: 4.5, large: 3, nontext: 3, info: 0 }

/* ---------- 跑 ---------- */

const css = fs.readFileSync(CSS, 'utf8')
const themes = [
  { name: '亮色', tokens: { ...readBlock(css, ':root') } },
  { name: '暗色', tokens: { ...readBlock(css, ':root'), ...readBlock(css, '.dark') } },
]

let failures = 0
let checked = 0
let skipped = 0

for (const theme of themes) {
  console.log(`\n${'═'.repeat(74)}\n  ${theme.name}主题\n${'═'.repeat(74)}`)
  let lastLevel = ''
  for (const [level, label, fgKey, bgKey] of PAIRS) {
    if (level !== lastLevel) {
      const names = { text: '正文文字  门槛 4.5:1', large: '大号文字  门槛 3:1',
                      nontext: '非文本    门槛 3:1', info: '仅供参考  不断言' }
      console.log(`\n  ── ${names[level]} ──`)
      lastLevel = level
    }
    let fg, bg, ratio
    try {
      fg = resolve(theme.tokens, `var(--${fgKey})`)
      bg = resolve(theme.tokens, `var(--${bgKey})`)
      ratio = contrast(fg, bg)
    } catch (e) {
      console.log(`  ⚠️  ${label.padEnd(30)} 跳过：${e.message}`)
      skipped++
      continue
    }
    const min = THRESHOLD[level]
    const ok = level === 'info' ? null : ratio >= min
    if (ok === true) checked++
    if (ok === false) failures++
    const mark = ok === null ? '  ·' : ok ? '  ✅' : '  ❌'
    const need = level === 'info' ? '  —  ' : `${min.toFixed(1)}`
    console.log(`${mark} ${label.padEnd(30)} ${ratio.toFixed(2).padStart(6)}:1  需 ${need}  ${fg} on ${bg}`)
  }
}

console.log(`\n${'═'.repeat(74)}`)
if (skipped > 0) {
  console.log(`  ⚠️  有 ${skipped} 项被跳过 —— 跳过不等于通过，必须查清原因`)
}
if (failures === 0 && skipped === 0) {
  console.log(`  ✅ 全部通过（断言 ${checked} 项 / 双主题）`)
} else {
  console.log(`  ❌ ${failures} 项不达标，${skipped} 项跳过（共断言 ${checked} 项）`)
}
console.log('═'.repeat(74))
process.exit(failures === 0 && skipped === 0 ? 0 : 1)
