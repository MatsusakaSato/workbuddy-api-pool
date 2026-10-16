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
  await page.waitForSelector('nav, [data-slot="sidebar"]', { timeout: 10_000 }).catch(() => {})

  const navCount = await page.locator('[data-slot="sidebar"] a[href^="#/"]').count()
  record('侧边栏导航项 ≥ 4', navCount >= 4, `${navCount} 项`)

  for (const label of ['概览', '模型', '用量', '接入']) {
    const visible = await page.getByRole('link', { name: label, exact: true }).first().isVisible()
    record(`导航「${label}」可见`, visible)
  }

  const h1 = await page.locator('h1').first().textContent()
  record('页面标题正确', h1?.trim() === '概览', `h1="${h1?.trim()}"`)

  /* ---------------- 3. 概览页数据 ---------------- */
  console.log('\n【3】概览页数据')
  const statCount = await page.locator('dl dt').count()
  record('KPI 条渲染', statCount >= 5, `${statCount} 项`)

  // 按面板标题精确计数，而不是全页累加 —— 后者会把日志表的行数混进来，
  // 导致"账号表渲染正确"这类断言实际上是假通过。
  const byPanel = await page.evaluate(() =>
    Object.fromEntries(
      [...document.querySelectorAll('table')].map((t) => [
        t.closest('section')?.querySelector('h2')?.textContent?.trim() ?? '(无标题)',
        t.querySelectorAll('tbody tr').length,
      ]),
    ),
  )
  // 网关此刻有 2 个账号，所以账号表与签到表都应恰好 2 行
  record('账号表行数正确', byPanel['账号'] === 2, `账号表 ${byPanel['账号']} 行`)
  record('签到表行数正确', byPanel['签到'] === 2, `签到表 ${byPanel['签到']} 行`)
  record('请求日志表已渲染', typeof byPanel['最近请求'] === 'number', `${byPanel['最近请求']} 行`)

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

  /* ---------------- 6. 主题切换 ---------------- */
  console.log('\n【6】主题切换')
  const darkBefore = await page.evaluate(() => document.documentElement.classList.contains('dark'))
  await page.getByRole('button', { name: /切换到(暗色|亮色)/ }).click()
  await page.waitForTimeout(250)
  const darkAfter = await page.evaluate(() => document.documentElement.classList.contains('dark'))
  record('主题切换生效', darkBefore !== darkAfter, `${darkBefore} → ${darkAfter}`)

  const bodyBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
  record('背景色随主题变化', !!bodyBg, bodyBg)

  // 深色下截图
  if (wantShots && darkAfter) {
    mkdirSync(SHOTS, { recursive: true })
    await page.screenshot({ path: resolve(SHOTS, 'overview-dark.png'), fullPage: true })
  }

  // 切回亮色
  if (darkAfter) {
    await page.getByRole('button', { name: /切换到(暗色|亮色)/ }).click()
    await page.waitForTimeout(200)
  }

  /* ---------------- 7. 命令面板 ---------------- */
  console.log('\n【7】命令面板')
  const errorsBeforePalette = pageErrors.length
  await page.keyboard.press('ControlOrMeta+k')
  await page.waitForTimeout(450)

  const palette = await page.evaluate(() => ({
    dialogs: document.querySelectorAll('[role="dialog"]').length,
    cmdkRoot: document.querySelectorAll('[cmdk-root]').length,
    items: document.querySelectorAll('[cmdk-item]').length,
    groups: document.querySelectorAll('[cmdk-group-heading]').length,
  }))
  record('⌘K 打开命令面板', palette.dialogs > 0 && palette.cmdkRoot > 0, JSON.stringify(palette))
  record('命令面板有分组与可选项', palette.items >= 8 && palette.groups >= 2, `${palette.items} 项 / ${palette.groups} 组`)
  // 回归护栏：cmdk 缺少 <Command> 根节点时，一打开就抛 subscribe 相关异常
  const paletteErrors = pageErrors.slice(errorsBeforePalette)
  record('打开面板未抛异常', paletteErrors.length === 0, paletteErrors[0] ?? '')

  await page.keyboard.press('Escape')
  // Radix 关闭是两阶段：先立刻置 data-state="closed"（证明 Esc 被处理了），
  // 退场动画结束后才真正卸载。固定 sleep 会在动画边界上偶发假失败，
  // 所以分成"断言状态"+"轮询等待卸载"两步。
  await page.waitForTimeout(150)
  const closeState = await page.evaluate(() =>
    [...document.querySelectorAll('[role="dialog"]')].map((d) => d.getAttribute('data-state')),
  )
  record('Esc 触发关闭（进入 closed 状态）', closeState.every((s) => s === 'closed'), JSON.stringify(closeState))

  let remaining = -1
  for (let i = 0; i < 30; i++) {
    remaining = await page.evaluate(() => document.querySelectorAll('[role="dialog"]').length)
    if (remaining === 0) break
    await page.waitForTimeout(100)
  }
  record('命令面板完成卸载', remaining === 0, `剩余 ${remaining} 个 dialog`)

  /* ---------------- 8. 路由与深链翻译 ---------------- */
  console.log('\n【8】路由切换')
  // 用 href 点而不是可见名：侧栏项带角标，accessible name 会被角标数字污染
  for (const [label, hash_target] of [
    ['模型', '#/models'],
    ['用量', '#/usage'],
    ['接入', '#/connect'],
  ]) {
    await page.locator(`[data-slot="sidebar"] a[href="${hash_target}"]`).first().click()
    await page.waitForTimeout(400)
    const hash = await page.evaluate(() => location.hash)
    record(`点击「${label}」路由正确`, hash.startsWith(hash_target), hash)
  }

  /* ---------------- 8a. 模型页 ---------------- */
  console.log('\n【8a】模型页')
  await page.locator('[data-slot="sidebar"] a[href="#/models"]').first().click()
  await page.waitForTimeout(700)

  const modelStats = async () =>
    page.evaluate(() => {
      const table = document.querySelector('table')
      const rows = table ? [...table.querySelectorAll('tbody tr')] : []
      // 分组标题行是整行 colspan 的单元格，不算模型行
      const modelRows = rows.filter((r) => !r.querySelector('td[colspan]'))
      return {
        total: modelRows.length,
        free: modelRows.filter((r) => r.textContent.includes('免费')).length,
        unknown: modelRows.filter((r) => r.querySelector('td:last-child')?.textContent.trim() === '—').length,
        facets: [...document.querySelectorAll('[aria-label="按类别筛选"] button')].map((b) => b.textContent.trim()),
      }
    })

  const mAll = await modelStats()
  record('模型表加载全部 51 个', mAll.total === 51, `${mAll.total} 行`)
  record('分面按钮齐全（含计数）', mAll.facets.length === 4, mAll.facets.join(' | '))
  // 上游目录里 19 个模型没有倍率，必须以 "—" 呈现而不是 "免费"
  record('无倍率模型显示为 —（不是免费）', mAll.unknown === 19, `${mAll.unknown} 行`)

  // 免费分面
  await page.locator('[aria-label="按类别筛选"] button', { hasText: '免费' }).first().click()
  await page.waitForTimeout(400)
  const mFree = await modelStats()
  record('「免费」分面筛选生效', mFree.total === mAll.free && mFree.total < 51, `${mFree.total} 行`)
  record('免费分面内全部标为免费', mFree.free === mFree.total, `${mFree.free}/${mFree.total}`)
  record('分面状态写进 URL', /facet=free/.test(await page.evaluate(() => location.hash)), await page.evaluate(() => location.hash))

  // 搜索（用真实存在的家族名；目录里并没有 qwen）
  await page.locator('[aria-label="按类别筛选"] button', { hasText: '全部' }).first().click()
  await page.waitForTimeout(300)
  await page.getByLabel('搜索模型').fill('glm')
  await page.waitForTimeout(450)
  const mSearch = await modelStats()
  record('搜索筛选生效', mSearch.total > 0 && mSearch.total < 51, `搜索 "glm" 命中 ${mSearch.total} 个`)

  // 排序切换
  await page.getByLabel('搜索模型').fill('')
  await page.waitForTimeout(300)
  await page.getByLabel('排序方式').click()
  await page.waitForTimeout(300)
  await page.getByRole('option', { name: '按上下文' }).click()
  await page.waitForTimeout(450)
  const ctxSorted = await page.evaluate(() => {
    const table = document.querySelector('table')
    const rows = [...(table?.querySelectorAll('tbody tr') ?? [])].filter((r) => !r.querySelector('td[colspan]'))
    // 列序：模型ID(0) 名称(1) 区域(2) 上下文(3) 最大输出(4) 倍率(5)
    return rows.map((r) => r.querySelectorAll('td')[3]?.textContent.trim() ?? '')
  })
  // 降序：最大的是 1M（目录里多个 100 万上下文的模型），无上下文的 "—" 排最后
  record(
    '「按上下文」降序生效',
    ctxSorted[0] === '1M' && ctxSorted[ctxSorted.length - 1] === '—',
    `首=${ctxSorted[0]} 尾=${ctxSorted[ctxSorted.length - 1]}`,
  )

  if (wantShots) {
    await page.goto(`${BASE}/#/models`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(900)
    await page.screenshot({ path: resolve(SHOTS, 'models-light.png'), fullPage: true })
  }

  /* ---------------- 8b. 用量页 ---------------- */
  console.log('\n【8b】用量页')
  await page.goto(`${BASE}/#/usage`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)

  const usageInfo = await page.evaluate(() => ({
    chart: document.querySelectorAll('svg.recharts-surface').length,
    bars: document.querySelectorAll('.recharts-bar-rectangle').length,
    tables: document.querySelectorAll('table').length,
    kpis: document.querySelectorAll('dl dt').length,
    // recharts 的坐标轴文字用了自定义字体后会继承 body 字体
    axisFont: getComputedStyle(document.querySelector('body')).fontFamily,
  }))
  record('图表已渲染', usageInfo.chart === 1, `${usageInfo.chart} 个 svg`)
  record('柱状图有柱子', usageInfo.bars > 0, `${usageInfo.bars} 根`)
  record('两张聚合表已渲染', usageInfo.tables === 2, `${usageInfo.tables} 张表`)
  record('用量 KPI 条渲染', usageInfo.kpis === 6, `${usageInfo.kpis} 项`)

  // 窗口切换应触发请求并刷新 KPI
  await page.locator('button[aria-pressed]', { hasText: '近 90 天' }).first().click()
  await page.waitForTimeout(1200)
  const afterSwitch = await page.evaluate(() => location.hash)
  record('切换统计窗口写入 URL', /w=d90/.test(afterSwitch), afterSwitch)
  record('窗口切换后 KPI 仍渲染', (await page.locator('dl dd').count()) === 6, await page.locator('dl dd').first().textContent())

  // 指标切换：请求 / 积分 / tokens（旧面板有，新面板必须保留）
  const chartInfo = () =>
    page.evaluate(() => ({
      bars: document.querySelectorAll('.recharts-bar-rectangle').length,
      // 没有柱子时必须给出解释，否则空白坐标系看起来就是坏了
      emptyNote: [...document.querySelectorAll('p')]
        .map((p) => p.textContent)
        .find((t) => t.includes('柱高全为 0') || t.includes('还没有请求')) ?? null,
    }))

  record(
    '默认指标是「请求」',
    !/(^|[?&])m=/.test(await page.evaluate(() => location.hash)),
    await page.evaluate(() => location.hash),
  )

  await page.locator('[role="group"][aria-label="图表指标"] button', { hasText: '积分' }).click()
  await page.waitForTimeout(500)
  const creditsHash = await page.evaluate(() => location.hash)
  record('切到「积分」写入 m=credits', /m=credits/.test(creditsHash), creditsHash)
  record(
    '「积分」按钮为选中态',
    (await page
      .locator('[role="group"][aria-label="图表指标"] button[aria-pressed="true"]', { hasText: '积分' })
      .count()) === 1,
  )
  const creditsAxis = await page.evaluate(() =>
    [...document.querySelectorAll('.recharts-yAxis .recharts-cartesian-axis-tick-value')].map(
      (t) => t.textContent,
    ),
  )
  record('「积分」轴刻度不塌缩为 0', creditsAxis.every((t) => t === '0' || Number(t) > 0), creditsAxis.join(','))

  // 真实数据里积分恒为 0，所以这里应当走空状态分支；有柱子则不要求提示
  const creditsChart = await chartInfo()
  record(
    '「积分」全为 0 时给出解释而不是空白坐标系',
    creditsChart.bars > 0 || creditsChart.emptyNote !== null,
    creditsChart.bars > 0 ? `有 ${creditsChart.bars} 根柱子` : `提示：「${creditsChart.emptyNote}」`,
  )

  await page.locator('[role="group"][aria-label="图表指标"] button', { hasText: 'tokens' }).click()
  await page.waitForTimeout(500)
  const tokensHash = await page.evaluate(() => location.hash)
  record('切到「tokens」写入 m=tokens', /m=tokens/.test(tokensHash), tokensHash)
  // tokens 有真实数据（117），所以必须真的画出柱子
  const tokensChart = await chartInfo()
  record('切到「tokens」后真的画出柱子', tokensChart.bars > 0, `${tokensChart.bars} 根`)

  await page.locator('[role="group"][aria-label="图表指标"] button', { hasText: '请求' }).click()
  await page.waitForTimeout(500)
  const backHash = await page.evaluate(() => location.hash)
  record('切回「请求」从 URL 移除 m', !/m=/.test(backHash), backHash)

  if (wantShots) {
    // 截图用默认视图（近 7 天 · 请求），不要把测试留下的状态拍进去
    await page.goto(`${BASE}/#/usage`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1300)
    await page.screenshot({ path: resolve(SHOTS, 'usage-light.png'), fullPage: true })
  }

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
    await page.screenshot({ path: resolve(SHOTS, 'connect-light.png'), fullPage: true })
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

  // 回概览并截亮色图
  await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1400)
  if (wantShots) {
    mkdirSync(SHOTS, { recursive: true })
    await page.screenshot({ path: resolve(SHOTS, 'overview-light.png'), fullPage: true })
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
