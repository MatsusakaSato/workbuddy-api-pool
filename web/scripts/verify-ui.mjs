/**
 * 界面自动化验收。
 *
 * 为什么要有这个脚本：截图无法被自动化审查（模型读不了图），
 * 所以"看起来对不对"必须尽量转成可断言的量：溢出、对比度、
 * 等宽数字、控制台报错、关键元素是否渲染、深链是否翻译。
 * 每次改完样式跑一遍，避免靠肉眼回归。
 *
 * 用法：node scripts/verify-ui.mjs [--shots]
 * 前置：网关已在 127.0.0.1:8788 运行（./run.sh）
 */

import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const BASE = process.env.GATEWAY_URL ?? 'http://127.0.0.1:8788'
const SHOTS = resolve(HERE, '../../docs/screenshots')
const wantShots = process.argv.includes('--shots')

const results = []
const record = (name, ok, detail = '') => {
  results.push({ name, ok, detail })
  console.log(`  ${ok ? '✅' : '❌'} ${name}${detail ? `  ${detail}` : ''}`)
}

async function main() {
  const browser = await chromium.launch({ channel: 'chrome', headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

  const consoleErrors = []
  const pageErrors = []
  const failedRequests = []
  /** 本次导航加载的资源（用来断言"某个 chunk 是否在这一步被请求"） */
  let assetRequests = []
  /**
   * 整个会话累积加载的资源。
   * 需要它是因为 chunk 只会在**第一次**用到时真正发请求，之后走内存缓存：
   * 如果只看"当前这次导航"，等跑到接入页的断言时 chunk 早就加载过了，
   * 会看到空列表而误判成"没按需加载"。
   */
  const allAssetRequests = []
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text())
  })
  page.on('pageerror', (e) => pageErrors.push(e.message))
  page.on('requestfailed', (r) => failedRequests.push(`${r.url()} :: ${r.failure()?.errorText}`))
  page.on('response', (r) => {
    const path = new URL(r.url()).pathname
    if (!path.startsWith('/static/assets/')) return
    const name = path.split('/').pop()
    assetRequests.push(name)
    allAssetRequests.push(name)
  })

  /* ---------------- 1. 加载与密钥注入 ---------------- */
  console.log('\n【1】加载与密钥注入')
  const resp = await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
  record('首页返回 200', resp?.status() === 200, `HTTP ${resp?.status()}`)

  const keyInfo = await page.evaluate(() => {
    const k = window.__DSH_GATEWAY_KEY__ ?? ''
    return { present: !!k, isPlaceholder: k === '__GATEWAY_KEY__', masked: k.slice(0, 6) }
  })
  record('页面注入了真实密钥', keyInfo.present && !keyInfo.isPlaceholder, keyInfo.masked)

  // 密钥必然会出现在页面源码的注入脚本里（这是设计如此），
  // 真正要确保的是它**不被渲染成可见文本** —— 否则截图/投屏就会泄露。
  const keyVisible = await page.evaluate(() => /sk-wb-[A-Za-z0-9]/.test(document.body.innerText))
  record('密钥未渲染为可见文本', !keyVisible)

  // shiki（语法高亮）只允许在「接入」页加载。
  // 首屏若混进来，就说明懒加载失效了 —— 这是最容易悄悄退化的性能点。
  const HEAVY = /^(shell|python|javascript|typescript)-/
  const heavyOnFirst = assetRequests.filter((f) => HEAVY.test(f))
  record(
    '首屏未加载任何语法高亮 chunk',
    heavyOnFirst.length === 0,
    heavyOnFirst.length ? heavyOnFirst.join(', ') : `${assetRequests.length} 个资源全部与高亮无关`,
  )

  /* ---------------- 2. 外壳渲染 ---------------- */
  console.log('\n【2】外壳渲染')
  await page.waitForSelector('header nav', { timeout: 10_000 }).catch(() => {})

  // 导航已横排进顶栏（侧栏按需求移除）。
  // ★ 2026-10-16：「用量」并入「概览」后导航从 4 项变 3 项。
  const navCount = await page.locator('header nav a[href^="#/"]').count()
  record('顶栏导航项 = 3（用量已并入概览）', navCount === 3, `${navCount} 项`)
  record('侧栏已移除', (await page.locator('[data-slot="sidebar"]').count()) === 0)

  for (const label of ['概览', '模型', '接入']) {
    // 顶栏项带角标数字（accessible name 形如 "模型34"），所以用前缀匹配
    const visible = await page
      .getByRole('link', { name: new RegExp(`^${label}`) })
      .first()
      .isVisible()
    record(`导航「${label}」可见`, visible)
  }
  record(
    '导航里不再有「用量」（已合并）',
    (await page.locator('header nav a[href="#/usage"]').count()) === 0,
  )

  const h1 = await page.locator('h1').first().textContent()
  record('页面标题正确', h1?.trim() === '概览', `h1="${h1?.trim()}"`)

  /* ---------------- 3. 概览页数据（账号 + 用量 + 最近请求 同页） ---------------- */
  console.log('\n【3】概览页数据')

  // 三个区块必须同页。这是"合并"这条需求最直接的证据：
  // 少任何一个都说明合并只做了一半（比如用量区块还留在另一个路由上）。
  const sections = await page.evaluate(() =>
    [...document.querySelectorAll('main h2')].map((h) => h.textContent.trim()),
  )
  record(
    '账号 / 用量 / 最近请求 三个区块同页',
    ['账号', '用量', '最近请求'].every((t) => sections.includes(t)),
    sections.join(' | '),
  )

  // 顶部的"本会话"口径 KPI 已删除；现在页面上的 KPI 条**只有用量区块那一条**，
  // 它的口径是统计窗口（今天 / 近 7 天 / …），能与"按模型"表核对。
  const kpiInfo = await page.evaluate(() => {
    const strips = [...document.querySelectorAll('main dl')]
    return {
      strips: strips.length,
      labels: strips[0]
        ? [...strips[0].querySelectorAll('dt')].map((d) => d.textContent.trim())
        : [],
      text: strips.map((s) => s.textContent).join(' '),
    }
  })
  record('页面上只有一条 KPI 统计条', kpiInfo.strips === 1, `${kpiInfo.strips} 条`)
  record(
    'KPI 是用量窗口口径（6 项，无"本会话"字样）',
    kpiInfo.labels.length === 6 && !/本会话/.test(kpiInfo.text),
    kpiInfo.labels.join(' / '),
  )

  // 按面板标题精确计数，而不是全页累加 —— 后者会把日志表的行数混进来，
  // 导致"账号面板渲染正确"这类断言实际上是假通过。
  const byPanel = await page.evaluate(() =>
    Object.fromEntries(
      [...document.querySelectorAll('table')].map((t) => [
        t.closest('section')?.querySelector('h2')?.textContent?.trim() ?? '(无标题)',
        t.querySelectorAll('tbody tr').length,
      ]),
    ),
  )
  // ★ 账号面板**不再是表格**（去表格是本轮需求），所以它不在 byPanel 里。
  record('账号面板已不是表格', byPanel['账号'] === undefined, JSON.stringify(byPanel))
  // 用量明细与请求日志**保留表格**：逐行比对 + 列对齐是它们的核心价值
  record('「按模型」仍是表格（明细表保留）', '按模型' in byPanel)
  record('请求日志表已渲染', typeof byPanel['最近请求'] === 'number', `${byPanel['最近请求']} 行`)
  record('「签到」不再有独立的表', byPanel['签到'] === undefined)

  /* ---- 账号面板：列表 + 4 个字段 + 余额量级条 ---- */
  const accountPanel = await page.evaluate(() => {
    const sec = [...document.querySelectorAll('section')].find((s) =>
      s.querySelector('h2')?.textContent?.includes('账号'),
    )
    if (!sec) return null

    const list = sec.querySelector('ul[aria-label="账号列表"]')
    const rows = list ? [...list.children] : []
    // 列头：面板里第一个 grid 行（4 个 span）
    const headEl = sec.querySelector('div.grid')
    const heads = headEl ? [...headEl.children].map((c) => c.textContent.trim()) : []

    // 每个单元格的"可见墨迹"边界：取文字 rect。
    // 用 Range 而不是 element.getBoundingClientRect()，因为后者量的是盒子 ——
    // 被 truncate 截断的文字盒子仍是满宽，会把"看着很挤"判成"很宽松"。
    const inkOf = (el) => {
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
      let left = Infinity
      let right = -Infinity
      let node
      while ((node = walker.nextNode())) {
        if (!node.textContent || !node.textContent.trim()) continue
        const range = document.createRange()
        range.selectNodeContents(node)
        for (const rect of range.getClientRects()) {
          if (rect.width === 0) continue
          left = Math.min(left, rect.left)
          right = Math.max(right, rect.right)
        }
      }
      return left === Infinity ? null : { left, right }
    }

    return {
      tableCount: sec.querySelectorAll('table').length,
      rowCount: rows.length,
      heads,
      checkboxes: sec.querySelectorAll(
        'button[role="checkbox"], input[type="checkbox"]',
      ).length,
      hasClaimAll: [...sec.querySelectorAll('button')].some((b) =>
        b.textContent.includes('立即签到'),
      ),
      hasAutoFooter: /自动签到/.test(sec.textContent),
      rows: rows.map((tr) => {
        const cells = [...tr.children]
        // 余额格 = 同时含「条」与「数字」的那个格子。
        //
        // ⚠️ 不要写死 cells[2]：2026-10-17 给余额格加了一行明细（基础/奖励拆分），
        // 行的直接子元素因此多了一个节点，按位置取会取到**明细**而不是条，
        // 于是"每行都有条"这条断言假性失败（实测 0/5）。
        // 按结构找比按序号找稳：条是唯一带 style.width 百分比的元素。
        const balanceCell = cells.find((c) =>
          [...c.querySelectorAll('span')].some((s) => /^\d+(\.\d+)?%$/.test(s.style.width)),
        )
        return {
          cellCount: cells.length,
          texts: cells.map((c) => c.textContent.trim()),
          // 余额条：填充块的宽度百分比（style.width = "NN%"）
          barPct: balanceCell
            ? (() => {
                const fill = [...balanceCell.querySelectorAll('span')].find((s) =>
                  /^\d+(\.\d+)?%$/.test(s.style.width),
                )
                return fill ? parseFloat(fill.style.width) : null
              })()
            : null,
          // 两桶拆分明细：基础 / 奖励
          splitText: balanceCell ? balanceCell.textContent.replace(/\s+/g, ' ').trim() : '',
          inks: cells.map(inkOf),
        }
      }),
    }
  })

  record('账号面板不再是表格', accountPanel?.tableCount === 0, `${accountPanel?.tableCount} 个 table`)
  record(
    '账号面板是列表（ul + li）',
    (accountPanel?.rowCount ?? 0) >= 1,
    `${accountPanel?.rowCount} 行`,
  )
  record(
    '账号行恰好 4 个字段：账号 / 用户名 / 余额 / 是否签到',
    accountPanel?.heads.length === 4 &&
      accountPanel.heads.join('/') === '账号/用户名/余额/是否签到',
    (accountPanel?.heads ?? []).join(' / '),
  )
  record(
    '每行都是 4 个格子（与列头一一对应）',
    (accountPanel?.rows ?? []).every((r) => r.cellCount === 4),
    (accountPanel?.rows ?? []).map((r) => r.cellCount).join(','),
  )
  // 勾选框 / 批量选择 / 二次确认是"半自动签到"的形态，签到改全自动后必须消失
  record(
    '签到表单已移除（没有勾选框）',
    accountPanel?.checkboxes === 0,
    `${accountPanel?.checkboxes} 个勾选框`,
  )
  record('页脚有「立即签到」兜底按钮', accountPanel?.hasClaimAll === true)
  record('页脚写明自动签到的状态', accountPanel?.hasAutoFooter === true)

  // 用户名与账号各司其职：账号列是纯 uin，用户名列是昵称
  const NICK = /Rain\.|乐乐|\d{3}\*{4}\d{4}/
  const uinCells = (accountPanel?.rows ?? []).map((r) => r.texts[0])
  const nickCells = (accountPanel?.rows ?? []).map((r) => r.texts[1])
  record(
    '账号列是纯 uin（11–12 位数字）',
    uinCells.length > 0 && uinCells.every((t) => /^\d{11,12}$/.test(t)),
    uinCells.join(' / '),
  )
  record(
    '用户名列展示了昵称',
    nickCells.length > 0 && nickCells.some((t) => NICK.test(t)),
    nickCells.join(' / '),
  )
  record(
    '昵称未混进账号列',
    !uinCells.some((t) => NICK.test(t)),
    uinCells.join(' / '),
  )

  /* ---- 余额：横向条，长度按池内最大余额归一 ----
     归一化是这条图形能成立的前提：上游只给剩余积分、没有可用的分母，
     所以"满格 = 池内最富的那个"。若有人改成给绝对值配一个拍脑袋的上限，
     这条断言会失败 —— 那正是我们希望被拦住的时候。 */
  const balances = (accountPanel?.rows ?? []).map((r) => {
    // 余额格文本形如 "1,851.1 基础 0 奖励 1,851.1"：**第一个数值是合计**，
    // 后面两个是拆分。取第一个而不是整串去噪 —— 后者会把三个数粘成一个。
    const firstNumber = ((r.texts[2] ?? '').match(/\d[\d,]*(?:\.\d+)?/) ?? [''])[0]
    return {
      pct: r.barPct,
      value: Number(firstNumber.replace(/,/g, '')),
      text: firstNumber,
      split: r.splitText,
    }
  })
  const known = balances.filter((b) => b.pct !== null && Number.isFinite(b.value))
  const maxValue = known.length ? Math.max(...known.map((b) => b.value)) : 0
  const fullest = known.filter((b) => b.value === maxValue)
  record(
    '每行都有余额量级条',
    known.length === (accountPanel?.rows ?? []).length && known.length > 0,
    `${known.length}/${accountPanel?.rows?.length} 行有条`,
  )
  record(
    '余额最大的账号条满格（按池内最大值归一）',
    fullest.length > 0 && fullest.every((b) => Math.round(b.pct) === 100),
    `最大余额 ${maxValue} → ${fullest.map((b) => b.pct + '%').join('/')}`,
  )
  /* 两桶必须**同时**在界面上，且合计 = 基础 + 奖励。
     这条锁住 2026-10-17 的修复：余额来自两个桶（基础套餐月底清零 / 平台奖励过期晚），
     只显示合计会把这个差异藏起来；拆错口径（例如把上游 TotalDosage 当余额）会让
     合计高于两桶之和。 */
  const splitRows = (accountPanel?.rows ?? []).filter((r) => /基础/.test(r.splitText ?? ''))
  record(
    '每行都显示「基础 / 奖励」拆分',
    splitRows.length === (accountPanel?.rows ?? []).length && splitRows.length > 0,
    `${splitRows.length}/${accountPanel?.rows?.length} 行有拆分`,
  )
  record(
    '合计与两桶之和一致（余额口径自洽）',
    (() => {
      // 用 textContent 拼接时元素之间**没有空白**（"基础已用完·奖励1,851.1"），
      // 所以这里用 "·" 与标签本身作分隔，而不是 \s+。
      const re = /基础(已用完|-?[\d,.]+)·?奖励(-?[\d,.]+)/
      return (accountPanel?.rows ?? []).every((r) => {
        const m = re.exec(r.splitText ?? '')
        if (!m) return false
        const base = m[1] === '已用完' ? 0 : Number(m[1].replace(/,/g, ''))
        const reward = Number(m[2].replace(/,/g, ''))
        if (!Number.isFinite(base) || !Number.isFinite(reward)) return false
        const total = Number(((r.texts[2] ?? '').match(/\d[\d,]*(?:\.\d+)?/) ?? [''])[0].replace(/,/g, ''))
        return Math.abs(base + reward - total) <= 0.2
      })
    })(),
    (accountPanel?.rows ?? []).slice(0, 2).map((r) => r.splitText).join(' | '),
  )

  record(
    '条长随余额单调（不是装饰）',
    (() => {
      const sorted = [...known].sort((a, b) => b.value - a.value).map((b) => b.pct)
      return sorted.every((p, i) => i === 0 || p <= sorted[i - 1] + 0.5)
    })(),
    known.map((b) => `${b.value}→${b.pct}%`).join(' '),
  )

  /* ---- 行内间距：右对齐的数字后面还跟着一列，必须留够 ----
     背景：旧表格里「余额」右对齐、「今日签到」左对齐，两者内容在格子边界相遇，
     实测最小间距只剩 36px。新版是 grid，但风险一样存在 ——
     所以量的仍然是"相邻格子可见墨迹的间距"，而不是盒子间距。 */
  const gapPairs = []
  for (const row of accountPanel?.rows ?? []) {
    for (let i = 0; i < row.inks.length - 1; i += 1) {
      const a = row.inks[i]
      const b = row.inks[i + 1]
      if (a && b) gapPairs.push({ gap: Math.round(b.left - a.right), pair: `${i}→${i + 1}` })
    }
  }
  const minGap = gapPairs.length ? Math.min(...gapPairs.map((p) => p.gap)) : -Infinity
  const worstGap = gapPairs.find((p) => p.gap === minGap)
  record(
    '账号行相邻字段可见内容间距 ≥ 40px',
    gapPairs.length > 0 && minGap >= 40,
    `最小 ${minGap}px（第 ${worstGap?.pair ?? '—'} 对），共 ${gapPairs.length} 对`,
  )

  /* ---- 版面顺序：最近请求必须在**最底部** ----
     这是本轮需求的原文（"最近几次请求依旧放到最底部，因为一般不会去看"）。
     用 DOM 顺序而不是位置比较：位置会随滚动/折叠变化，顺序才是设计意图。 */
  const order = await page.evaluate(() => {
    const pool = [...document.querySelectorAll('main h2')]
    const idx = (t) => pool.findIndex((h) => h.textContent.trim() === t)
    return {
      账号: idx('账号'),
      用量: idx('用量'),
      最近请求: idx('最近请求'),
      all: pool.map((h) => h.textContent.trim()),
    }
  })
  record(
    '版面顺序：账号 → 用量 → 最近请求（日志在最底）',
    order.账号 >= 0 && order.账号 < order.用量 && order.用量 < order.最近请求,
    order.all.join(' → '),
  )


  const hasUin = await page.locator('text=/330100178623|330120998846/').count()
  record('账号 ID 出现在页面上', hasUin > 0, `${hasUin} 处`)

  const noError = await page.locator('text=界面出错了').count()
  record('未落入错误边界', noError === 0)

  /* ---------------- 4. 字体与数字对齐 ---------------- */
  console.log('\n【4】字体与数字对齐')
  const fontInfo = await page.evaluate(() => {
    const body = getComputedStyle(document.body).fontFamily
    const tnumEl = document.querySelector('.tnum')
    const tnum = tnumEl ? getComputedStyle(tnumEl).fontVariantNumeric : ''
    return { body, tnum }
  })
  record('正文字体为 Geist', /Geist/i.test(fontInfo.body), fontInfo.body.slice(0, 48))
  record('tnum 生效（表格数字不抖动）', /tabular-nums/.test(fontInfo.tnum), fontInfo.tnum)

  /* ---------------- 5. 溢出检查 ---------------- */
  console.log('\n【5】溢出检查')
  const overflow = await page.evaluate(() => {
    const de = document.documentElement
    const docOverflow = de.scrollWidth - de.clientWidth
    // 找出真正越界的元素（排除自身可滚动的容器）
    const bad = []
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect()
      if (r.width === 0) continue
      if (r.right > de.clientWidth + 1.5) {
        const style = getComputedStyle(el)
        const scrollable = ['auto', 'scroll'].includes(style.overflowX)
        if (!scrollable) {
          bad.push(`${el.tagName.toLowerCase()}.${(el.className || '').toString().slice(0, 40)} right=${Math.round(r.right)}`)
        }
      }
    }
    return { docOverflow, bad: bad.slice(0, 6) }
  })
  record('页面无横向溢出', overflow.docOverflow <= 1, `doc ${overflow.docOverflow}px`)
  record(
    '无元素越出视口',
    overflow.bad.length === 0,
    overflow.bad.length ? overflow.bad[0] : '',
  )

  /* ---------------- 5a. 窄屏（新增的可视化列会带来宽度压力） ----------------
     账号面板从表格改成 4 列 grid，用量明细表也从 8 列变成 9 列
     （占比 / 延迟分布 / 趋势）—— 两者都容易在窄屏顶破布局。
     这里在 768 / 900 / 1280 三档下断言**文档级**不溢出 ——
     表格自身可以被 overflow-x-auto 的容器横向滚动，那是设计允许的；
     但文档整体不该出现横向滚动条。
     ★ 2026-10-16：用量并入概览后，这里改成在**合并页**上验证（原来打开的是 /usage）。 */
  console.log('\n【5a】窄屏无横向溢出')
  for (const width of [768, 900, 1280]) {
    await page.setViewportSize({ width, height: 900 })
    await page.goto(`${BASE}/#/overview`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(900)
    const narrow = await page.evaluate(() => {
      const de = document.documentElement
      return {
        doc: de.scrollWidth - de.clientWidth,
        // 表格必须被可滚动祖先包住，否则会顶破文档
        tableContained: [...document.querySelectorAll('table')].every((t) => {
          let el = t.parentElement
          while (el) {
            if (['auto', 'scroll'].includes(getComputedStyle(el).overflowX)) return true
            el = el.parentElement
          }
          return false
        }),
        // 账号行是 grid：[minmax(11rem,1.2fr) minmax(7rem,0.9fr) minmax(14rem,1.3fr) minmax(7rem,0.7fr)]
        // 四个最小值相加 39rem = 624px，768px 视口下（内容区还要减 padding）本该刚好放下。
        accountRows: document.querySelectorAll('ul[aria-label="账号列表"] > li').length,
      }
    })
    record(
      `${width}px 下合并页无横向溢出`,
      narrow.doc <= 1,
      `doc ${narrow.doc}px`,
    )
    record(`${width}px 下聚合表被滚动容器包住`, narrow.tableContained)
    record(`${width}px 下账号面板仍渲染`, narrow.accountRows >= 1, `${narrow.accountRows} 行`)
  }
  await page.setViewportSize({ width: 1440, height: 900 })

  /* ---------------- 6. 固定浅色（不做主题切换） ---------------- */
  console.log('\n【6】固定浅色')
  const themeState = await page.evaluate(() => ({
    hasDarkClass: document.documentElement.classList.contains('dark'),
    colorScheme: getComputedStyle(document.documentElement).colorScheme,
    bodyBg: getComputedStyle(document.body).backgroundColor,
  }))
  record('未挂 .dark 类（固定浅色）', !themeState.hasDarkClass, `.dark 存在=${themeState.hasDarkClass}`)
  record('color-scheme 为 light（原生控件/滚动条也走浅色）', themeState.colorScheme === 'light', themeState.colorScheme)

  // 断言"真的是浅色"而不是"有颜色就行"：三个通道都该接近白
  const bgRgb = themeState.bodyBg.match(/\d+/g)?.map(Number) ?? []
  const isLightBg = bgRgb.length >= 3 && bgRgb.slice(0, 3).every((c) => c > 200)
  record('背景色确为浅色（三通道 > 200）', isLightBg, themeState.bodyBg)

  // 界面上不应再有任何主题切换入口
  const themeControls = await page
    .getByRole('button', { name: /切换到(暗色|亮色)|主题/ })
    .count()
  record('界面上没有主题切换按钮', themeControls === 0, `${themeControls} 个`)

  // 旧深链 ?theme=dark 应当既不生效、也不留在地址栏
  await page.goto(`${BASE}/?theme=dark`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(500)
  const legacyTheme = await page.evaluate(() => ({
    hasDarkClass: document.documentElement.classList.contains('dark'),
    search: location.search,
  }))
  record('旧 ?theme=dark 深链不再生效', !legacyTheme.hasDarkClass)
  record('旧 ?theme=dark 已从地址栏清除', !/theme=/.test(legacyTheme.search), `search="${legacyTheme.search}"`)

  /* ---------------- 7. 搜索已下线 ---------------- */
  console.log('\n【7】搜索已下线（⌘K 与命令面板移除）')
  // ⌘K 不再打开任何对话框；页面里也不应有 cmdk 残留
  const errorsBeforePalette = pageErrors.length
  await page.keyboard.press('ControlOrMeta+k')
  await page.waitForTimeout(450)
  const noPalette = await page.evaluate(() => ({
    dialogs: document.querySelectorAll('[role="dialog"]').length,
    cmdkRoot: document.querySelectorAll('[cmdk-root]').length,
  }))
  record('⌘K 不再打开命令面板', noPalette.dialogs === 0 && noPalette.cmdkRoot === 0, JSON.stringify(noPalette))
  record('面板移除未抛异常', pageErrors.slice(errorsBeforePalette).length === 0)

  /* ---------------- 8. 路由与深链翻译 ---------------- */
  console.log('\n【8】路由切换')
  // 导航已横排进顶栏（侧栏移除）；用 href 点而不是可见名：
  // 顶栏项带角标，accessible name 会被角标数字污染
  for (const [label, hash_target] of [
    ['概览', '#/overview'],
    ['模型', '#/models'],
    ['接入', '#/connect'],
  ]) {
    await page.locator(`header nav a[href="${hash_target}"]`).first().click()
    await page.waitForTimeout(400)
    const hash = await page.evaluate(() => location.hash)
    record(`点击「${label}」路由正确`, hash.startsWith(hash_target), hash)
  }

  // 旧的 #/usage 必须被**重定向**到 #/overview，而不是掉进"未知路径"或 404。
  // 而且 hash 里的窗口参数要跟着走：丢掉它等于把用户选好的统计口径静默换回默认。
  await page.goto(`${BASE}/#/usage?w=d90`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(900)
  const redirected = await page.evaluate(() => ({
    hash: location.hash,
    h1: document.querySelector('main h1')?.textContent ?? '',
  }))
  record(
    '旧 #/usage 重定向到概览（含窗口参数）',
    redirected.hash === '#/overview?w=d90' && redirected.h1 === '概览',
    `hash=${redirected.hash} h1=${redirected.h1}`,
  )
  const switchedWindow = await page.evaluate(() =>
    [...document.querySelectorAll('button[aria-pressed="true"]')].map((b) => b.textContent.trim()),
  )
  record(
    '重定向后窗口选择仍生效（近 90 天）',
    switchedWindow.includes('近 90 天'),
    switchedWindow.join(' / ') || '(没有选中项)',
  )

  /* ---------------- 8a. 模型页 ---------------- */
  console.log('\n【8a】模型页')
  await page.locator('header nav a[href="#/models"]').first().click()
  /* 等卡片真的渲染出来，而不是死等 700ms。
     这里原来写的是 waitForTimeout(700)，在模型目录接口较慢时会假失败：
     读到 0 张卡就报"模型卡片加载全部 51 个 ❌"，但重跑一次又好了。
     这种 flake 比没有断言更糟 —— 会让人开始无视红色。
     现在改成等第一张卡出现（带超时），加载慢只会变慢，不会误判。 */
  await page
    .locator('main button[aria-label^="复制模型 ID"]')
    .first()
    .waitFor({ state: 'attached', timeout: 15_000 })
    .catch(() => {
      /* 超时也继续往下走：让下面的断言去报真实的失败信息（0 张），
         而不是在这里抛一个跟业务无关的 Playwright 超时。 */
    })
  // 卡片是分批渲染的，再给一个短的稳定期让总数到齐
  await page.waitForTimeout(300)

  // 卡片矩阵布局（models.tsx）：每个模型是一张 shadow-border-subtle 小卡，不再是表格行。
  // 取"含复制模型 ID 按钮"的卡片节点，避免把外层容器误算进去。
  const modelCardSel = () =>
    [...document.querySelectorAll('main [class*="shadow-border-subtle"]')].filter((el) =>
      el.querySelector('button[aria-label^="复制模型 ID"]'),
    )

  const modelStats = async () =>
    page.evaluate(() => {
      const cards = [...document.querySelectorAll('main [class*="shadow-border-subtle"]')].filter((el) =>
        el.querySelector('button[aria-label^="复制模型 ID"]'),
      )
      return {
        total: cards.length,
        free: cards.filter((c) => c.textContent.includes('免费')).length,
        unknown: cards.filter((c) => c.textContent.includes('倍率 —')).length,
        facets: [...document.querySelectorAll('[aria-label="按类别筛选"] button')].map((b) => b.textContent.trim()),
      }
    })

  const mAll = await modelStats()
  record('模型卡片加载全部 51 个', mAll.total === 51, `${mAll.total} 张`)
  record('分面按钮齐全（含计数）', mAll.facets.length === 2, mAll.facets.join(' | '))
  // 上游目录里 19 个模型没有倍率，必须以 "—" 呈现而不是 "免费"
  record('无倍率模型显示为 —（不是免费）', mAll.unknown === 19, `${mAll.unknown} 张`)

  // 免费分面
  await page.locator('[aria-label="按类别筛选"] button', { hasText: '免费' }).first().click()
  await page.waitForTimeout(400)
  const mFree = await modelStats()
  record('「免费」分面筛选生效', mFree.total === mAll.free && mFree.total < 51, `${mFree.total} 行`)
  record('免费分面内全部标为免费', mFree.free === mFree.total, `${mFree.free}/${mFree.total}`)
  record('分面状态写进 URL', /facet=free/.test(await page.evaluate(() => location.hash)), await page.evaluate(() => location.hash))

  // 搜索框已按需求移除；确认页面上没有搜索输入残留
  await page.locator('[aria-label="按类别筛选"] button', { hasText: '全部' }).first().click()
  await page.waitForTimeout(300)
  record(
    '模型页无搜索框残留',
    (await page.getByLabel('搜索模型').count()) === 0 && (await page.locator('input[placeholder*="搜索"]').count()) === 0,
  )

  // 排序切换
  await page.getByLabel('排序方式').click()
  await page.waitForTimeout(300)
  await page.getByRole('option', { name: '按上下文' }).click()
  await page.waitForTimeout(450)
  const ctxSorted = await page.evaluate(() => {
    const cards = [...document.querySelectorAll('main [class*="shadow-border-subtle"]')].filter((el) =>
      el.querySelector('button[aria-label^="复制模型 ID"]'),
    )
    // 卡片参数条形如 "上下文 1M 输出 64K"，抽取上下文值
    return cards.map((c) => /上下文\s*([^\s]+)/.exec(c.textContent)?.[1] ?? '')
  })
  // 降序：最大的是 1M（目录里多个 100 万上下文的模型），无上下文的 "—" 排最后
  record(
    '「按上下文」降序生效',
    ctxSorted[0].startsWith('1M') && ctxSorted[ctxSorted.length - 1].startsWith('—'),
    `首=${ctxSorted[0]} 尾=${ctxSorted[ctxSorted.length - 1]}`,
  )

  if (wantShots) {
    await page.goto(`${BASE}/#/models`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(900)
    await page.screenshot({ path: resolve(SHOTS, 'models.png'), fullPage: true })
  }

  /* ---------------- 8b. 用量区块（在合并后的概览页里） ----------------
     ★ 2026-10-16：用量不再是独立页面。这里刻意仍然用 `#/usage` 打开 ——
     顺便验证旧地址的重定向链路，且断言的内容与原来一致（图形、口径、悬停）。 */
  console.log('\n【8b】用量区块（概览页内）')
  await page.goto(`${BASE}/#/usage`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)

  const usageInfo = await page.evaluate(() => {
    // 每日走势图：柱 + 折线并存。柱与命中区都是 rect，用 fill/class 区分。
    const svg = document.querySelector('main svg[aria-label*="每日走势"]')
    const rects = svg ? [...svg.querySelectorAll('rect')] : []
    return {
      bars: rects.filter((r) => (r.getAttribute('class') ?? '').includes('fill-chart')).length,
      // 整列命中区（透明）—— 悬停用的，数量应与柱子一致
      hitZones: rects.filter((r) => r.getAttribute('fill') === 'transparent').length,
      polylines: svg ? svg.querySelectorAll('polyline').length : 0,
      // SVG 高度必须被显式钉住，否则 preserveAspectRatio="none" 会把盒子拉长
      svgHeight: svg ? Math.round(svg.getBoundingClientRect().height) : 0,
      // 分布条：每个聚合行一条，aria-label 里带 p50/p90/p99
      distBars: document.querySelectorAll('main [role="img"][aria-label*="延迟分布"]').length,
      sparks: [...document.querySelectorAll('main svg[aria-label*="天趋势"]')].length,
      tables: document.querySelectorAll('table').length,
      kpis: document.querySelectorAll('dl dt').length,
      // recharts 已移除：任何残留的 recharts 容器都说明改版没生效
      recharts: document.querySelectorAll('.recharts-wrapper, svg.recharts-surface').length,
    }
  })
  // 柱与折线必须**同时**存在 —— 这是本次需求的核心（"柱状图和折线图并存"）
  record('每日走势柱状图已渲染', usageInfo.bars >= 7, `${usageInfo.bars} 根柱`)
  record('每日走势折线已渲染', usageInfo.polylines === 1, `${usageInfo.polylines} 条折线`)
  record(
    '柱与折线并存（不是二选一）',
    usageInfo.bars >= 7 && usageInfo.polylines === 1,
    `${usageInfo.bars} 柱 + ${usageInfo.polylines} 线`,
  )
  // 悬停命中区：整列可悬停，否则细柱子根本点不到
  record(
    '每根柱子都有悬停命中区',
    usageInfo.hitZones === usageInfo.bars && usageInfo.bars > 0,
    `${usageInfo.hitZones} 命中区 / ${usageInfo.bars} 柱`,
  )
  // 高度必须被钉住：踩过坑，只写 w-full 时 SVG 被拉到约 1800px 高
  record(
    'SVG 高度被显式约束（未被 viewBox 宽高比拉长）',
    usageInfo.svgHeight > 0 && usageInfo.svgHeight <= 200,
    `${usageInfo.svgHeight}px`,
  )
  // ★ 2026-10-16：只剩「按模型」一张明细表（「按账号」表已删 ——
  // 账号的展示按需求收敛成 4 个字段，见 accounts-checkin-panel.tsx）。
  record('分布条已渲染（按模型每一行一条）', usageInfo.distBars >= 2, `${usageInfo.distBars} 条`)
  record('行内趋势条已渲染', usageInfo.sparks >= 2, `${usageInfo.sparks} 条`)
  // 合并页上有两张表：按模型 + 最近请求。少一张说明某个区块没渲染。
  record('按模型表 + 请求日志表已渲染', usageInfo.tables === 2, `${usageInfo.tables} 张表`)
  record('用量 KPI 条渲染', usageInfo.kpis === 6, `${usageInfo.kpis} 项`)
  // 改版把项目里唯一的图表库依赖移除了，这里防止它悄悄回来
  record('recharts 已完全移除', usageInfo.recharts === 0, `${usageInfo.recharts} 个残留容器`)

  // 悬停必须给出**模型明细** —— 这是柱+线相比日历图真正的增量
  const hoverBox = await page.evaluate(() => {
    const svg = document.querySelector('main svg[aria-label*="每日走势"]')
    const hit = [...svg.querySelectorAll('rect')].filter((r) => r.getAttribute('fill') === 'transparent')
    const bb = hit[hit.length - 1].getBoundingClientRect()
    return { x: bb.x + bb.width / 2, y: bb.y + bb.height / 2, zones: hit.length }
  })
  await page.mouse.move(hoverBox.x, hoverBox.y)
  await page.waitForTimeout(400)
  const hoverText = await page.evaluate(() => {
    const panels = [...document.querySelectorAll('main div')].filter(
      (d) =>
        String(d.className).includes('absolute') && String(d.className).includes('ring-border'),
    )
    return panels[0]?.innerText ?? ''
  })
  record(
    '悬停显示当天请求数',
    /\d/.test(hoverText) && /次/.test(hoverText),
    hoverText.split('\n').slice(0, 2).join(' / '),
  )
  record(
    '悬停显示按模型明细',
    /deepseek|glm/i.test(hoverText),
    hoverText.split('\n').slice(0, 5).join(' / '),
  )
  // 明细之和必须等于当天总数 —— 服务端从同一份聚合反推，这条守住口径一致。
  // 浮层结构：日期 / "N 次" / "tokens" / 总量 / [失败] / "模型" / "tokens · 次数" /
  //           然后每个模型两行：模型名、然后 "147M · 1352"。
  // 注意 innerText 会把 "名 · 数" 拆行呈现，所以按"模型名行 + 数值行"配对解析。
  const sumCheck = await page.evaluate(() => {
    const panels = [...document.querySelectorAll('main div')].filter(
      (d) => String(d.className).includes('absolute') && String(d.className).includes('ring-border'),
    )
    const lines = (panels[0]?.innerText ?? '').split('\n').map((x) => x.trim()).filter(Boolean)
    const reqTotal = Number((lines.find((l) => /^[\d,]+ 次$/.test(l)) ?? '').replace(/[^\d]/g, ''))
    // 数值行形如 "147M · 1352" / "420 · 2"：· 后面是请求数
    let reqSum = 0
    let tokRows = 0
    for (const l of lines) {
      const m = /^([\d.]+\s*[kKmM万亿]?)\s*·\s*([\d,]+)$/.exec(l)
      if (m) {
        tokRows++
        reqSum += Number(m[2].replace(/[^\d]/g, ''))
      }
    }
    return { reqTotal, reqSum, tokRows }
  })
  record('悬停明细行数 >= 1', sumCheck.tokRows >= 1, `${sumCheck.tokRows} 行明细`)
  record(
    '悬停明细请求数之和等于当天总数',
    sumCheck.reqSum > 0 && sumCheck.reqSum === sumCheck.reqTotal,
    `明细合计 ${sumCheck.reqSum} / 总数 ${sumCheck.reqTotal}`,
  )

  // 折线必须代表 tokens 而不是请求数 —— 两者数量级不同，坐标应显著分离
  const axisCheck = await page.evaluate(() => {
    const svg = document.querySelector('main svg[aria-label*="每日走势"]')
    const line = svg?.querySelector('polyline')
    const bars = [...svg.querySelectorAll('rect')].filter((r) =>
      (r.getAttribute('class') ?? '').includes('fill-chart'),
    )
    const ly = line ? Number(line.getAttribute('points').split(' ').pop().split(',')[1]) : null
    const by = bars.length ? Number(bars[bars.length - 1].getAttribute('y')) : null
    return { ly, by }
  })
  record(
    '折线走 token 轴（与柱子的 y 不同源）',
    axisCheck.ly !== null && axisCheck.by !== null && Math.abs(axisCheck.ly - axisCheck.by) > 0.5,
    `线 y=${axisCheck.ly?.toFixed(1)} 柱 y=${axisCheck.by?.toFixed(1)}`,
  )

  // 窗口切换应触发请求并刷新 KPI
  await page.locator('button[aria-pressed]', { hasText: '近 90 天' }).first().click()
  await page.waitForTimeout(1200)
  const afterSwitch = await page.evaluate(() => location.hash)
  record('切换统计窗口写入 URL', /w=d90/.test(afterSwitch), afterSwitch)
  record('窗口切换后 KPI 仍渲染', (await page.locator('dl dd').count()) === 6, await page.locator('dl dd').first().textContent())
  // 90 天窗口下应当画出 90 根柱子
  const barsAfter = await page.evaluate(() => {
    const svg = document.querySelector('main svg[aria-label*="每日走势"]')
    return svg
      ? [...svg.querySelectorAll('rect')].filter((r) =>
          (r.getAttribute('class') ?? '').includes('fill-chart'),
        ).length
      : 0
  })
  record('切到 90 天后画出 90 根柱子', barsAfter === 90, `${barsAfter} 根`)

  // 新增的「今天」窗口
  record(
    '窗口选择器含「今天」',
    (await page.locator('[role="group"][aria-label="统计窗口"] button', { hasText: '今天' }).count()) === 1,
  )
  await page.locator('[role="group"][aria-label="统计窗口"] button', { hasText: '今天' }).click()
  await page.waitForTimeout(1400)
  const todayHash = await page.evaluate(() => location.hash)
  record('切到「今天」写入 w=d1', /w=d1/.test(todayHash), todayHash)
  // 口径是今天，但图表必须保留足够上下文（否则单根柱子看不出走势）
  const todayInfo = await page.evaluate(() => {
    const svg = document.querySelector('main svg[aria-label*="每日走势"]')
    const bars = svg
      ? [...svg.querySelectorAll('rect')].filter((r) =>
          (r.getAttribute('class') ?? '').includes('fill-chart'),
        ).length
      : 0
    const dd = [...document.querySelectorAll('main dl dd')].map((d) => d.textContent)
    return { bars, firstKpi: dd[0] ?? null, kpiCount: dd.length }
  })
  record(
    '「今天」口径下图表仍保留至少 30 天上下文',
    todayInfo.bars >= 30,
    `${todayInfo.bars} 根柱`,
  )
  record('「今天」口径下 KPI 仍渲染', todayInfo.kpiCount === 6, `首个 KPI=${todayInfo.firstKpi}`)

  /* 分布条的语义断言：tooltip 必须给出分位数，否则它只是一条彩色装饰 */
  const distInfo = await page.evaluate(() => {
    const bar = document.querySelector('main [role="img"][aria-label*="延迟分布"]')
    return {
      label: bar?.getAttribute('aria-label') ?? '',
      // 分布条必须真的画出分段（至少两种色块），全同色说明四档没有生效
      segments: bar ? new Set([...bar.children].map((c) => getComputedStyle(c).backgroundColor)).size : 0,
    }
  })
  record('分布条带 p50/p90/p99 语义标签', /p50/.test(distInfo.label) && /p99/.test(distInfo.label), distInfo.label.slice(0, 60))
  record('分布条分出多个色阶', distInfo.segments >= 2, `${distInfo.segments} 档`)

  /* 截图：这里原本会单独拍一张 usage.png。
     「用量」并入概览后它已经是同一页，再拍一张只会得到两个高度重叠的图 ——
     所以删除，改由脚本末尾那张 overview.png（fullPage）覆盖整页。 */

  /* ---------------- 8c. 接入页 ---------------- */
  console.log('\n【8c】接入页')
  // 清空资源记录，这样下面「按需加载了语法 chunk」才是本次导航的证据
  assetRequests = []
  await page.goto(`${BASE}/#/connect`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(900)

  const connectInfo = await page.evaluate(() => {
    const text = document.body.innerText
    return {
      hasBaseUrl: /http:\/\/127\.0\.0\.1:8788\/v1/.test(text),
      codeBlocks: document.querySelectorAll('[data-slot="code-block"]').length,
      copyButtons: document.querySelectorAll('[data-slot="code-block-copy-button"]').length,
      // 默认（未开显示）时页面上不应出现完整密钥
      leaksKey: /sk-wb-[A-Za-z0-9]{8,}/.test(text),
    }
  })
  record('Base URL 正确展示', connectInfo.hasBaseUrl)
  record('四个代码片段已渲染', connectInfo.codeBlocks === 4, `${connectInfo.codeBlocks} 个`)
  record('每个片段都有复制按钮', connectInfo.copyButtons === 4, `${connectInfo.copyButtons} 个`)
  record('默认不泄露完整密钥', !connectInfo.leaksKey)

  // 显示密钥开关
  await page.getByRole('button', { name: /显示密钥/ }).click()
  await page.waitForTimeout(300)
  const revealed = await page.evaluate(() => /sk-wb-[A-Za-z0-9]{8,}/.test(document.body.innerText))
  record('「显示密钥」开关生效', revealed)
  await page.getByRole('button', { name: /隐藏密钥/ }).click()
  await page.waitForTimeout(300)
  const rehidden = await page.evaluate(() => /sk-wb-[A-Za-z0-9]{8,}/.test(document.body.innerText))
  record('「隐藏密钥」可再次遮住', !rehidden)

  /* ---- 语法高亮：颜色必须来自 --tok-*，且接入页才加载 shiki ---- */
  // 等多两帧让动态 import 的语法 chunk 落地
  await page.waitForTimeout(2000)
  const highlight = await page.evaluate(() => {
    const colored = [...document.querySelectorAll('[data-slot="code-block-content"] span[style*="color"]')]
    const vars = new Set()
    for (const el of colored) {
      const m = el.getAttribute('style')?.match(/var\((--tok-[a-z]+)\)/)
      if (m) vars.add(m[1])
    }
    const perBlock = [...document.querySelectorAll('[data-slot="code-block-content"]')].map(
      (pre) => pre.querySelectorAll('span[style*="color"]').length,
    )
    return {
      tokens: colored.length,
      vars: [...vars].sort(),
      perBlock,
      // 纯文本回退会渲染成没有 span 的裸文本
      plainBlocks: perBlock.filter((n) => n === 0).length,
      totalBlocks: perBlock.length,
    }
  })
  record('代码片段已高亮成 token', highlight.tokens > 20, `${highlight.tokens} 个 token`)
  record(
    '高亮颜色取自 --tok-* 变量（深浅色自动跟随）',
    highlight.vars.length >= 4,
    highlight.vars.join(', '),
  )
  // 4 个片段里有 3 个带语言标签，第 4 个「任意客户端」故意不给语言 → 保持纯文本
  record(
    '无法识别语言的片段保持纯文本',
    highlight.plainBlocks === 1 && highlight.totalBlocks === 4,
    `${highlight.totalBlocks - highlight.plainBlocks}/4 个高亮，${highlight.plainBlocks} 个纯文本`,
  )
  const heavyOnConnect = allAssetRequests.filter((f) =>
    /^(shell|python|javascript|typescript)-/.test(f),
  )
  record(
    '接入页按需加载了语法 chunk',
    heavyOnConnect.length >= 2,
    heavyOnConnect.length ? heavyOnConnect.join(', ') : '（一个都没加载，说明懒加载坏了）',
  )

  if (wantShots) {
    await page.screenshot({ path: resolve(SHOTS, 'connect.png'), fullPage: true })
  }

  /* ---------------- 8d. 旧深链翻译 ---------------- */
  console.log('\n【8d】旧深链翻译')
  // 旧深链：?tab=models&facet=free&sort=cost&q=glm 应被翻译成 #/models?… 并从查询串移除
  await page.goto(`${BASE}/?tab=models&facet=free&sort=cost&q=glm`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(700)
  const translated = await page.evaluate(() => ({ hash: location.hash, search: location.search }))
  record('旧深链翻译为 hash 路由', translated.hash.startsWith('#/models'), translated.hash)
  record('旧查询参数已从地址栏清除', translated.search === '', `search="${translated.search}"`)
  record(
    'facet/sort/q 被带入 hash',
    /facet=free/.test(translated.hash) && /sort=cost/.test(translated.hash) && /q=glm/.test(translated.hash),
    translated.hash,
  )
  // 翻译过来的筛选要真的作用于列表。注意 glm 模型里没有免费的，
  // 所以 facet=free 与 q=glm 叠加后应当是 0 行 —— 这恰好证明两个条件都生效了。
  const carried = await modelStats()
  const badge = await page.locator('text=/^0 \\/ 51$/').count()
  record(
    '翻译来的筛选条件真的生效',
    carried.total === 0 && badge > 0,
    `q=glm + facet=free 交叉后 ${carried.total} 行（应为 0）`,
  )

  /* 新格式 hash 深链必须**原样保留**。
     这里曾经有个严重 bug：启动时的旧深链翻译无条件按 ?tab=（缺省 overview）
     重算路由并 replaceState 覆盖 hash，于是直接打开 #/models 会立刻被打回
     #/overview —— 刷新、书签、分享链接全部失效，只有概览页能深链。
     下面这几条就是钉住这个回归。

     ★ 2026-10-16：`#/usage` 不再列在这里 —— 它是**已合并的旧地址**，
     正确行为是被改写成 `#/overview`（单独在下面断言），而不是"原样保留"。 */
  for (const [hash, wantH1] of [
    ['#/models', '模型'],
    ['#/connect', '接入'],
    ['#/overview', '概览'],
  ]) {
    await page.goto(`${BASE}/${hash}`, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(1400)
    const got = await page.evaluate(() => ({
      hash: location.hash,
      h1: document.querySelector('main h1')?.textContent ?? '',
    }))
    record(
      `hash 深链 ${hash} 不被改写（刷新/书签可用）`,
      got.hash === hash && got.h1 === wantH1,
      `hash=${got.hash} h1=${got.h1}`,
    )
  }

  /* 旧地址 `#/usage`：应该被**翻译**成 `#/overview`，且 hash 自己的 query 要跟着走。
     旧书签进过别人的地址栏，静默失效（或掉进未知路径）是最糟的处理方式。 */
  await page.goto(`${BASE}/#/usage?w=d30`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1400)
  const legacyHash = await page.evaluate(() => ({
    hash: location.hash,
    h1: document.querySelector('main h1')?.textContent ?? '',
  }))
  record(
    '旧 hash #/usage 被翻译成 #/overview（query 一并保留）',
    legacyHash.hash === '#/overview?w=d30' && legacyHash.h1 === '概览',
    `hash=${legacyHash.hash} h1=${legacyHash.h1}`,
  )

  // 带 query 的 hash 也要连 query 一起保留
  await page.goto(`${BASE}/#/models?q=glm`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1400)
  const hashQuery = await page.evaluate(() => location.hash)
  record('hash 里的 query 同样不被抹掉', hashQuery === '#/models?q=glm', hashQuery)

  // 没有 hash 时，旧深链仍要能翻译（本次修复不能把旧兼容弄坏）。
  // ?tab=usage 现在应当落到概览（用量已合并进它）。
  await page.goto(`${BASE}/?tab=usage`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1400)
  const legacyTab = await page.evaluate(() => location.hash)
  record('无 hash 时旧 ?tab=usage 翻译到概览', legacyTab === '#/overview', legacyTab)

  // ?tab=usage&w=d90：旧"用量页深链 + 窗口选择"必须整体迁到概览并保持窗口
  await page.goto(`${BASE}/?tab=usage&w=d90`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1400)
  const legacyWindow = await page.evaluate(() => location.hash)
  record(
    '旧 ?tab=usage&w= 的窗口参数被带进概览',
    legacyWindow === '#/overview?w=d90',
    legacyWindow,
  )

  // 回概览并截亮色图
  await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1400)
  if (wantShots) {
    mkdirSync(SHOTS, { recursive: true })
    await page.screenshot({ path: resolve(SHOTS, 'overview.png'), fullPage: true })
  }

  /* ---------------- 9. 控制台洁净度 ---------------- */
  console.log('\n【9】控制台与网络')
  // React Router 的未来标志警告、favicon 之类的噪音要排除掉
  const realErrors = consoleErrors.filter(
    (t) => !/favicon|Download the React DevTools|React Router Future Flag/i.test(t),
  )
  record('无未捕获的运行时异常', pageErrors.length === 0, pageErrors[0] ?? '')
  record('无控制台错误', realErrors.length === 0, realErrors[0] ?? '')

  const realFailures = failedRequests.filter((u) => !/favicon/.test(u))
  record('无失败请求', realFailures.length === 0, realFailures[0] ?? '')

  await browser.close()

  /* ---------------- 汇总 ---------------- */
  const failed = results.filter((r) => !r.ok)
  console.log(`\n${'═'.repeat(60)}`)
  console.log(`总计 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length}`)
  if (failed.length) {
    console.log('\n失败项：')
    for (const f of failed) console.log(`  ❌ ${f.name}  ${f.detail}`)
  }
  console.log('═'.repeat(60))
  process.exit(failed.length === 0 ? 0 : 1)
}

main().catch((e) => {
  console.error('\n验收脚本自身出错：', e)
  process.exit(2)
})
