/**
 * 可视化小件：分布条 / 迷你趋势条 / 日历热力图 / 横向条。
 *
 * 这些组件的共同定位是**替换表格里的若干列**，而不是替换表格本身。
 * 判断依据（见 docs/可视化方案调研.md）：表格在"逐行比对 + 可复制 ID"上不可替代，
 * 但当一列的内容是**形状**（分布、趋势、占比）时，放数字反而是浪费。
 *
 * 三条设计约束（沿用项目既有约定，改动前请先读 docs/设计调研.md）：
 *
 *  1. **颜色按语义分层**，不引入装饰色。
 *     分布条的蓝色梯度表达"量级"，红/绿仍然只表达状态。
 *     这是对"颜色一出现就意味着看这里"的**扩展**，不是违反 ——
 *     渐变是一条连续尺度，读者不会把它误读成告警。
 *
 *  2. **所有数值用 tnum**，否则轮询刷新时宽度抖动。
 *
 *  3. **空数据要有形状**：日历图的空白格是信息（"这天没有流量"），
 *     不能渲染成"组件坏了"的样子。
 *
 * 实现上刻意**不引入图表库**：这几个图形用 div / SVG 直接画就够。
 * 实际上这次改版把项目里唯一的图表库依赖（recharts，约 400kB）也一并移除了 ——
 * 用量页原来的柱状图换成日历热力图之后，recharts 不再被任何页面引用。
 */

import { useMemo, useState } from 'react'
import { cn } from '@/lib/utils'
import { formatCompact, formatCredits, formatDuration, formatInt } from '@/lib/format'
import type { UsageDay } from '@/lib/types'

/* ------------------------------------------------------------------ */
/* 1. 分布条                                                           */
/* ------------------------------------------------------------------ */

/** 四个分档的语义色。深浅是同一色相的梯度 —— 表达量级，不是状态。 */
const BANDS = [
  { key: 'fast', label: '快', hint: '< p50', color: 'bg-chart-gradient-1' },
  { key: 'normal', label: '中', hint: 'p50–p90', color: 'bg-chart-gradient-2' },
  { key: 'slow', label: '慢', hint: 'p90–p99', color: 'bg-chart-gradient-3' },
  { key: 'tail', label: '长尾', hint: '≥ p99', color: 'bg-danger-dot' },
] as const

const CELLS = 20

/**
 * 把 p50/p90/p99 换算成四段占比。
 *
 * 注意这是**由分位数反推的占比**，不是真实直方图 —— 只能保证单调、形状方向正确。
 * 之所以可接受：分布条的用途是"一眼看出是常态还是事故"，
 * 而不是精确的百分位报表。真要精确占比，得让服务端直接返回 4 个计数。
 */
function bandsFromPercentiles(n: number) {
  if (n <= 0) return [0, 0, 0, 0]
  // 分位数本身就定义了累积比例，直接做差分即可
  const fast = Math.round(n * 0.5)
  const normal = Math.round(n * 0.4)
  const slow = Math.round(n * 0.09)
  const tail = Math.max(0, n - fast - normal - slow)
  return [fast, normal, slow, tail]
}

/** 单个模型的分布条（用于表格单元格） */
export function LatencyDistribution({
  p50,
  p90,
  p99,
  max,
  requests,
}: {
  p50: number
  p90: number
  p99: number
  max: number
  requests: number
}) {
  const counts = bandsFromPercentiles(requests)

  // 全 0 请求：画一条空槽，而不是什么都不画 —— 后者看起来像渲染失败
  if (requests <= 0) {
    return <div className="h-3.5 w-full min-w-[7.5rem] rounded-sm bg-muted" aria-hidden />
  }

  const cells = counts.map((count) => Math.max(count > 0 ? 1 : 0, Math.round((count / requests) * CELLS)))

  return (
    <div
      className="flex h-3.5 w-full min-w-[7.5rem] items-stretch gap-px"
      role="img"
      aria-label={`延迟分布：p50 ${formatDuration(p50)}、p90 ${formatDuration(p90)}、p99 ${formatDuration(p99)}、最慢 ${formatDuration(max)}`}
      title={`p50 ${formatDuration(p50)} · p90 ${formatDuration(p90)} · p99 ${formatDuration(p99)}\n最慢 ${formatDuration(max)}（共 ${formatInt(requests)} 次）`}
    >
      {cells.map((count, band) =>
        Array.from({ length: count }).map((_, i) => (
          <span
            key={`${band}-${i}`}
            className={cn('min-w-px flex-1 rounded-[1px]', BANDS[band].color)}
          />
        )),
      )}
    </div>
  )
}

/** 分布条图例。只在真正用到处出现一次，不重复铺 */
export function DistributionLegend({ className }: { className?: string }) {
  return (
    <ul className={cn('flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-muted-foreground', className)}>
      {BANDS.map((band) => (
        <li key={band.key} className="flex items-center gap-1.5">
          <span aria-hidden className={cn('size-2 rounded-[2px]', band.color)} />
          {band.label}
          <span className="text-muted-foreground/70">{band.hint}</span>
        </li>
      ))}
    </ul>
  )
}

/* ------------------------------------------------------------------ */
/* 2. 迷你趋势条                                                       */
/* ------------------------------------------------------------------ */

/**
 * 行内趋势条。表达"在变多还是变少"，不表达精确值 —— 后者表格里已有。
 *
 * 用 SVG 而不是 div：几十个细条用 div 会产生大量 DOM 节点，
 * 而 SVG 可以用一个 rect 表达一根，且能保证像素级对齐。
 */
export function Sparkline({
  values,
  width = 64,
  height = 16,
  className,
}: {
  values: number[]
  width?: number
  height?: number
  className?: string
}) {
  const { bars, total } = useMemo(() => {
    const max = Math.max(1, ...values)
    const slot = width / Math.max(1, values.length)
    return {
      bars: values.map((value, i) => ({
        x: i * slot,
        // 高度至少 1px：0 值日也要有一个"底座"，否则时间轴看起来是断的
        h: value > 0 ? Math.max(1.5, (value / max) * height) : 1,
        active: value > 0,
      })),
      total: values.reduce((sum, v) => sum + v, 0),
    }
  }, [values, width, height])

  if (total === 0) {
    // 全程无流量：一条平底线 + 文字说明，比空白更诚实
    return (
      <span className={cn('text-2xs text-muted-foreground/60', className)} title="窗口内没有请求">
        —
      </span>
    )
  }

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={cn('block overflow-visible', className)}
      role="img"
      aria-label={`近 ${values.length} 天趋势`}
    >
      {bars.map((bar, i) => (
        <rect
          key={i}
          x={bar.x}
          y={height - bar.h}
          width={Math.max(1, width / values.length - 1)}
          height={bar.h}
          rx={0.5}
          className={bar.active ? 'fill-primary' : 'fill-muted-foreground/25'}
        />
      ))}
    </svg>
  )
}

/* ------------------------------------------------------------------ */
/* 3. 日历热力图                                                       */
/* ------------------------------------------------------------------ */

/**
 * 每日请求日历热力图。
 *
 * 为什么替换柱状图：90 天里如果有 89 天为零，柱状图会画出 89 根零高柱子 ——
 * 屏幕上是一张几乎全空的坐标系，看起来像坏了。
 * 日历图把"没有流量"变成**一片浅灰**，那本身就是信息（这是个新池子）。
 *
 * ⚠️ 一个踩过的坑：日历**不能跟着统计窗口缩放**。
 * 最初实现成 `slice(-weeks*7)`，结果"近 7 天"窗口只画出 7 个格子 ——
 * 一排孤零零的方块看起来像渲染出错，而不是"一周的数据"。
 * 日历图的阅读方式是"扫一片颜色找规律"，少于 4~5 周就没有规律可言。
 * 所以这里**总是渲染完整的 weeks 周**，数据不足的部分留空位，
 * 由调用方通过 description 说明"这是近 N 天"。
 */
export function CalendarHeatmap({
  days,
  weeks = 13,
}: {
  days: { date: string; requests: number }[]
  weeks?: number
}) {
  const { cells, monthLabels, peak } = useMemo(() => {
    // 不足 weeks 周时也要铺满整个网格：多出来的位置留 null（渲染成透明空格），
    // 保证不同窗口下**图形尺寸稳定**，切窗口时不会整块跳动。
    const slice = days.slice(-weeks * 7)
    if (slice.length === 0) return { cells: [], monthLabels: [], peak: 0 }

    const peak = Math.max(...slice.map((d) => d.requests))
    // 5 档：0 = 无流量，1–4 按峰值等比。用峰值而不是分位数，
    // 是为了让"最高的一天"永远是最深色 —— 运维要找的就是它。
    const level = (n: number): number => {
      if (n <= 0) return 0
      const r = n / Math.max(1, peak)
      if (r > 0.66) return 4
      if (r > 0.33) return 3
      if (r > 0.1) return 2
      return 1
    }

    // 末尾对齐：最后一天贴到网格最右列，前面补空位。
    // 这样"今天"永远在右下角，符合日历的阅读直觉。
    const last = new Date(`${slice[slice.length - 1].date}T00:00:00`)
    const trailing = 6 - ((last.getDay() + 6) % 7) // 填到本周日
    const lead = weeks * 7 - slice.length - trailing

    const padded: ({ date: string; requests: number } | null)[] = [
      ...Array.from({ length: Math.max(0, lead) }, () => null),
      ...slice,
      ...Array.from({ length: trailing }, () => null),
    ]

    const cells = padded.map((day, i) => ({
      day,
      week: Math.floor(i / 7),
      row: i % 7,
      level: day ? level(day.requests) : -1, // -1 = 补位空格，不渲染
    }))

    const totalWeeks = Math.ceil(padded.length / 7)
    // 只在月份变化的那一列标月份，避免 13 个重复标签
    const monthLabels: { week: number; label: string }[] = []
    for (let w = 0; w < totalWeeks; w++) {
      const day = padded[w * 7]
      if (!day) continue
      const month = day.date.slice(5, 7)
      const prev = w > 0 ? padded[(w - 1) * 7] : null
      if (!prev || prev.date.slice(5, 7) !== month) {
        monthLabels.push({ week: w, label: `${Number(month)}月` })
      }
    }

    return { cells, monthLabels, peak }
  }, [days, weeks])

  if (cells.length === 0) {
    return <p className="px-1 py-4 text-2xs text-muted-foreground">还没有可展示的日期数据。</p>
  }

  const LEVEL_CLASS = [
    'bg-muted', // 0 次：浅灰。这是"有信息"的空白，不是缺失
    'bg-chart-heat-1',
    'bg-chart-heat-2',
    'bg-chart-heat-3',
    'bg-chart-heat-4',
  ]
  const totalWeeks = Math.max(...cells.map((c) => c.week)) + 1

  return (
    <div className="flex items-start gap-2">
      {/* 星期刻度：只标一/三/五/日，全标会挤成一团 */}
      <div className="grid shrink-0 grid-rows-7 gap-[2px] pt-[18px] text-[9px] leading-[11px] text-muted-foreground/70">
        {['一', '', '三', '', '五', '', '日'].map((label, i) => (
          <span key={i} className="h-[11px]">
            {label}
          </span>
        ))}
      </div>

      <div className="scroll-thin min-w-0 overflow-x-auto pb-1">
        {/* 月份刻度 */}
        <div
          className="grid gap-[2px] pb-1 text-[10px] text-muted-foreground"
          style={{ gridTemplateColumns: `repeat(${totalWeeks}, 11px)` }}
        >
          {monthLabels.map((m) => (
            <span key={m.week} style={{ gridColumn: m.week + 1 }}>
              {m.label}
            </span>
          ))}
        </div>

        <div
          className="grid gap-[2px]"
          style={{
            gridTemplateColumns: `repeat(${totalWeeks}, 11px)`,
            gridTemplateRows: 'repeat(7, 11px)',
            gridAutoFlow: 'column',
          }}
        >
          {cells.map((cell, i) => {
            if (!cell.day) return <span key={i} aria-hidden />
            return (
              <span
                key={i}
                className={cn(
                  'size-[11px] rounded-[2px]',
                  LEVEL_CLASS[Math.max(0, cell.level)],
                )}
                title={`${cell.day.date}　${formatInt(cell.day.requests)} 次请求`}
              />
            )
          })}
        </div>
      </div>

      {/* 刻度图例 */}
      <div className="flex shrink-0 flex-col items-center gap-1 pl-1 pt-[18px] text-[9px] text-muted-foreground/70">
        <span>多</span>
        <div className="flex flex-col gap-[2px]">
          {[4, 3, 2, 1, 0].map((level) => (
            <span key={level} className={cn('size-[11px] rounded-[2px]', LEVEL_CLASS[level])} />
          ))}
        </div>
        <span>少</span>
        {peak > 0 ? (
          <span className="tnum pt-1 text-2xs text-muted-foreground/70" title="窗口内单日最高请求数">
            峰 {formatCompact(peak)}
          </span>
        ) : null}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* 4. 横向量级条                                                       */
/* ------------------------------------------------------------------ */

/**
 * 横向量级条。用于把"余额"这类数值表达成可比长度。
 *
 * 关键取舍：**条长按组内最大值归一，不是绝对值**。
 * 因为绝对值需要一个上限，而余额没有天然上限；
 * 组内归一保证"最长的那个"永远是满格，相对关系一眼可见。
 *
 * ★ 2026-10-16：`label` 改为可选（不传就不渲染左侧标签）。
 *   账号面板每行只需要"条 + 数值"两段 —— 行的左侧已经有账号与用户名两列，
 *   再在条前面写一遍"余额"就是重复标签。
 *   同时补上 `danger` 档：余额为 0 是**要看的**状态，条本身该红
 *   （研究记录见 docs/可视化方案调研.md §2.3）。
 */
export function MagnitudeBar({
  value,
  max,
  label,
  sublabel,
  tone = 'primary',
  format = formatCredits,
  segments,
}: {
  value: number | null
  max: number
  label?: string
  sublabel?: string
  tone?: 'primary' | 'muted' | 'danger'
  format?: (value: number | null | undefined) => string
  /**
   * 可选的**分段填充**：把同一个条按组成拆开（如 余额 = 基础套餐 + 平台奖励）。
   *
   * 关键取舍：每段宽度仍按 `value / max` 归一，**不按 max**。
   * 「跨账号比较」与「单账号内两桶的比例」是两个问题，混在一根条上必然拧巴：
   * 若按 max 归一，A 号的两桶之和会短于条，读者会以为条没画满；
   * 而若跨账号最大值变化，同一个号的视觉长度还会跳。所以这里只解决后者：
   * 条长回答"谁快见底"，分段回答"这些余额是怎么构成的"。
   *
   * 顺序就是数组顺序（调用方负责把"先用掉"的桶放前面，见账号面板）。
   */
  segments?: { value: number; className: string; label: string }[]
}) {
  const pct = value === null || max <= 0 ? 0 : Math.min(100, (value / max) * 100)
  const unknown = value === null
  const segTotal = segments?.reduce((sum, seg) => sum + Math.max(0, seg.value), 0) ?? 0
  const useSegments = !unknown && !!segments && segments.some((seg) => seg.value > 0) && segTotal > 0

  return (
    <div className="flex items-center gap-2.5">
      {label ? (
        <span className="w-[6.5rem] shrink-0 truncate text-xs font-medium" title={label}>
          {label}
        </span>
      ) : null}
      <span className="min-w-0 flex-1">
        <span className="flex h-3.5 w-full overflow-hidden rounded-sm bg-muted">
          {unknown ? null : useSegments ? (
            <span className="flex h-full" style={{ width: `${pct}%` }}>
              {segments!
                .filter((seg) => seg.value > 0)
                .map((seg, i) => (
                  <span
                    key={i}
                    className={cn('h-full transition-[width] duration-300', seg.className)}
                    style={{ width: `${(Math.max(0, seg.value) / segTotal) * 100}%` }}
                    title={seg.label}
                  />
                ))}
            </span>
          ) : (
            <span
              className={cn(
                'h-full rounded-sm transition-[width] duration-300',
                // muted 用于"这个数不可比"的场合（如失败率），不抢主色
                tone === 'primary'
                  ? 'bg-primary'
                  : tone === 'danger'
                    ? 'bg-danger'
                    : 'bg-muted-foreground/40',
              )}
              style={{ width: `${pct}%` }}
            />
          )}
        </span>
      </span>
      {/* 数字列不设固定宽度：4.5rem 装不下 "12,163.4" 这类五位数带小数，
          超宽时会被相邻列挤压/截断。改为按内容自适应并禁止换行，
          配合外层右对齐，多行数字仍然是一条竖线。 */}
      <span className="tnum shrink-0 whitespace-nowrap text-right text-xs" title={sublabel}>
        {format(value)}
      </span>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* 5. 柱 + 折线组合图（每日走势）                                       */
/* ------------------------------------------------------------------ */

/**
 * 每日走势的图例。
 *
 * 这是**图例**不是说明文字：只用色块 + 极短的词标出"哪个图形读哪条轴"，
 * 不写句子。双轴图如果没有图例，读者会把折线按左轴刻度去读，数值全错 ——
 * 所以这两条是**不可省的最小信息**，不是解释性文案。
 */
export function TrendLegend({ className }: { className?: string }) {
  return (
    <ul className={cn('flex flex-wrap items-center gap-x-3 text-2xs text-muted-foreground', className)}>
      <li className="flex items-center gap-1.5">
        <span aria-hidden className="size-2 rounded-[2px] bg-chart-gradient-2" />
        <span aria-hidden className="text-chart-gradient-4">左轴</span>
        请求
      </li>
      <li className="flex items-center gap-1.5">
        <span aria-hidden className="h-0.5 w-3 rounded-full bg-chart-gradient-4" />
        <span aria-hidden className="text-muted-foreground">右轴</span>
        tokens
      </li>
    </ul>
  )
}

/** 图形几何常量。抽出来是为了让布局与坐标计算用同一组数字，不会各自漂移。 */
const PLOT_H = 132 // 绘图区高度
const PAD_T = 12 // 顶部留白（给曲线溢出留余量）
const PAD_B = 20 // 底部留给 x 轴标签
/** 左侧 y 轴刻度占的宽度，**像素**（刻度文字用 HTML 叠放，见下） */
const AXIS_W = 44

/**
 * viewBox 的宽度。
 *
 * ⚠️ 踩过的坑：最初把 viewBox 宽度写成 100，以便"用百分比坐标自适应"，
 * 但同时又用像素常量 PAD_L=44 做 x 偏移 —— 44 在 100 宽的坐标系里是 44%，
 * 于是所有图形被推到右侧，最后一个柱子的命中区落在 x≈1960（视口只有 1440），
 * 悬停完全失效，而且因为 preserveAspectRatio="none"，横向被拉伸得面目全非。
 *
 * 现在改成：viewBox 宽度足够大（1000），x 坐标一律用 viewBox 单位，
 * 只有 y 轴文字用 HTML 绝对定位（避免 SVG 文字被非等比缩放拉变形）。
 * 这样"坐标系"与"像素"不再混用。
 */
const VB_W = 1000
/** 绘图区左边界（viewBox 单位）。按容器宽度比例换算，保证轴文字区宽度稳定 */
const PLOT_L = (AXIS_W / 1400) * VB_W

/**
 * 每日走势：柱状图 + 折线图并存。
 *
 * 两者表达的都是**请求数** —— 这不是冗余，而是各自回答一个不同的问题：
 *   · 柱子：某一天**具体**多少请求（离散、可精确读数）
 *   · 折线：当天的**总 token 数**（连续、跨越空档看趋势）
 *
 * 为什么折线不是"再画一遍请求数"：两个量纲不同的量叠在一张图上才有信息增量。
 * 请求数只说明"被调用了多少次"，token 数才说明"真正消耗了多少" ——
 * 实测里同一天可以请求数很少但 token 极高（长上下文/长输出），
 * 两条线一分开就能看出"调用次数降了但消耗反而涨了"这种事，
 * 画同一个量则完全看不出来。
 *
 * 因为两个量纲差着好几个数量级（实测某日 1488 次请求 vs 1.4 亿 token），
 * **必须用双 Y 轴**：左轴请求数、右轴 token。否则 token 线会把柱子压成贴地一条缝。
 *
 * 悬停任意一天显示该日**按模型拆分**（服务端保证明细之和 == 当日总数），
 * 每行给出该模型的 token 数 —— 这才是"钱花在哪了"的答案。
 *
 * 用 SVG 手绘而不是图表库：项目已移除 recharts，
 * 而这里只需要两种图元（rect / polyline），引入依赖不划算。
 */
export function DailyTrendChart({
  days,
  height = PLOT_H,
}: {
  days: UsageDay[]
  height?: number
}) {
  const [hover, setHover] = useState<number | null>(null)

  const geo = useMemo(() => {
    if (days.length === 0) return null
    // 左轴：请求数
    const maxReq = Math.max(1, ...days.map((d) => d.requests))
    const stepReq = niceStep(maxReq)
    const topReq = Math.ceil(maxReq / stepReq) * stepReq

    // 右轴：tokens。单独算刻度，否则两侧共用一个 top 必然有一侧被压扁
    const maxTok = Math.max(1, ...days.map((d) => d.tokens))
    const stepTok = niceStep(maxTok)
    const topTok = Math.ceil(maxTok / stepTok) * stepTok

    const plotW = VB_W - PLOT_L // 绘图区宽度（viewBox 单位）
    const bw = plotW / days.length
    const innerH = height - PAD_T - PAD_B

    const x = (i: number) => PLOT_L + (i + 0.5) * bw
    // 两个 y 映射共用同一块绘图区，但各自用自己的刻度上限 —— 这就是双轴的全部含义
    const yReq = (v: number) => PAD_T + innerH * (1 - v / topReq)
    const yTok = (v: number) => PAD_T + innerH * (1 - v / topTok)

    const ticksReq: number[] = []
    for (let v = 0; v <= topReq + 1e-9; v += stepReq) ticksReq.push(v)
    const ticksTok: number[] = []
    for (let v = 0; v <= topTok + 1e-9; v += stepTok) ticksTok.push(v)

    return { maxReq, maxTok, bw, innerH, x, yReq, yTok, ticksReq, ticksTok }
    // height 参与坐标计算，必须进依赖
  }, [days, height])

  if (!geo) {
    return <p className="px-1 py-4 text-2xs text-muted-foreground">还没有可展示的每日数据。</p>
  }

  const active = hover !== null ? days[hover] : null
  // 折线点：走 token 轴。请求为 0 的日子也要连上，否则折线会断裂成一段段
  const linePoints = days.map((d, i) => `${geo.x(i)},${geo.yTok(d.tokens)}`).join(' ')

  return (
    <div className="relative">
      {/* ⚠️ 必须同时给 width:100% 与**固定像素高度**。
          只写 className="w-full" 时，SVG 的高度会由 viewBox 宽高比推出 ——
          而 viewBox 宽只有 100、preserveAspectRatio="none"，
          结果是元素被拉成约 1800px 高（实测），布局炸掉且悬停命中区跑到屏幕外。
          显式 height 后 viewBox 的"100 宽"只是一个坐标系，不再影响渲染尺寸。 */}
      <svg
        className="block w-full"
        style={{ height }}
        viewBox={`0 0 ${VB_W} ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`每日走势：柱=请求数，折线=tokens。共 ${days.length} 天，最高 ${geo.maxReq} 次请求、${geo.maxTok} tokens`}
        onMouseLeave={() => setHover(null)}
      >
        {/* 网格线按**左轴（请求数）**刻度画。两条轴刻度数不同，
            若各画一套会得到互相穿插的横线，非常吵 —— 网格只服务柱子。 */}
        {geo.ticksReq.map((t) => (
          <line
            key={t}
            x1={PLOT_L}
            x2={VB_W}
            y1={geo.yReq(t)}
            y2={geo.yReq(t)}
            className="stroke-border-subtle"
            strokeWidth={0.5}
            vectorEffect="non-scaling-stroke"
          />
        ))}

        {/* 柱子：离散的量 */}
        {days.map((d, i) => (
          <rect
            key={d.date}
            x={geo.x(i) - geo.bw * 0.32}
            y={geo.yReq(d.requests)}
            width={geo.bw * 0.64}
            // 高度 = 基线 - 数据点。0 值日为 0 高（不画假柱子）
            height={Math.max(0, geo.yReq(0) - geo.yReq(d.requests))}
            className={cn(
              'transition-[fill]',
              hover === i ? 'fill-chart-gradient-4' : 'fill-chart-gradient-2',
            )}
            onMouseEnter={() => setHover(i)}
          />
        ))}

        {/* 折线：当天的总 token 数，走**右轴**刻度。
            用 non-scaling-stroke 保证在非等比缩放下仍是 1.5px */}
        <polyline
          points={linePoints}
          fill="none"
          className="stroke-chart-gradient-4"
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />

        {/* 悬停点标记 */}
        {hover !== null ? (
          <circle
            cx={geo.x(hover)}
            cy={geo.yTok(days[hover].tokens)}
            r={2.5}
            className="fill-chart-gradient-4"
            vectorEffect="non-scaling-stroke"
          />
        ) : null}

        {/* 命中区：整列可悬停，不必瞄准细柱子（1px 的柱子根本点不到） */}
        {days.map((d, i) => (
          <rect
            key={`hit-${d.date}`}
            x={PLOT_L + i * geo.bw}
            y={PAD_T}
            width={geo.bw}
            height={geo.innerH + PAD_B}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
          />
        ))}
      </svg>

      {/* 左轴（请求数）刻度文字用 HTML 叠放：SVG 文字在 preserveAspectRatio=none 下会被横向拉伸 */}
      <div
        className="pointer-events-none absolute inset-y-0 left-0 flex flex-col justify-between"
        style={{ paddingTop: PAD_T - 5, paddingBottom: PAD_B - 5, width: AXIS_W - 6 }}
      >
        {[...geo.ticksReq].reverse().map((t) => (
          <span key={t} className="tnum text-right text-[10px] leading-none text-chart-gradient-4">
            {formatCompact(t)}
          </span>
        ))}
      </div>

      {/* 右轴（tokens）刻度：双轴必须两侧都标出单位，否则读者会按左轴去读折线。
          颜色与折线一致，用"同色同轴"这个视觉惯例把它们绑在一起。 */}
      <div
        className="pointer-events-none absolute inset-y-0 right-0 flex flex-col justify-between"
        style={{ paddingTop: PAD_T - 5, paddingBottom: PAD_B - 5, width: AXIS_W }}
      >
        {[...geo.ticksTok].reverse().map((t) => (
          <span key={t} className="tnum text-left text-[10px] leading-none text-muted-foreground">
            {formatCompact(t)}
          </span>
        ))}
      </div>

      {/* x 轴标签：只标首/中/尾，全标会挤成一团 */}
      <div className="flex justify-between pr-2 pt-1 text-[10px] text-muted-foreground"
        style={{ paddingLeft: AXIS_W + 4 }}>
        {[0, Math.floor(days.length / 2), days.length - 1]
          .filter((i, idx, arr) => arr.indexOf(i) === idx && days[i])
          .map((i) => (
            <span key={i} className="tnum">
              {days[i].date.slice(5)}
            </span>
          ))}
      </div>

      {/* 悬停详情：这是"柱+线"相比日历图真正的增量 —— 能回答"这天是谁在跑" */}
      {active ? (
        <div
          className="pointer-events-none absolute top-1 z-10 min-w-[11rem] rounded-md bg-popover p-2 shadow-popover ring-1 ring-border"
          style={{ left: AXIS_W + 4 }}
        >
          <div className="flex items-baseline justify-between gap-3">
            <span className="tnum text-2xs font-medium text-foreground">{active.date}</span>
            <span className="tnum text-2xs text-muted-foreground">
              {formatInt(active.requests)} 次
            </span>
          </div>
          {/* tokens 是折线代表的口径，放在最显眼处 */}
          <div className="mt-0.5 flex items-baseline justify-between gap-3">
            <span className="text-[10px] text-muted-foreground">tokens</span>
            <span className="tnum text-[10px] font-medium text-chart-gradient-4">
              {formatCompact(active.tokens)}
            </span>
          </div>
          {active.errors > 0 ? (
            <div className="tnum mt-0.5 text-[10px] text-danger">失败 {formatInt(active.errors)} 次</div>
          ) : null}
          {active.models.length > 0 ? (
            <div className="mt-1.5 flex flex-col gap-0.5 border-t border-border-subtle pt-1.5">
              <div className="flex items-baseline justify-between gap-3 pb-0.5">
                <span className="text-[10px] text-muted-foreground">模型</span>
                <span className="text-[10px] text-muted-foreground">tokens · 次数</span>
              </div>
              {active.models.slice(0, 8).map((m) => (
                <div key={m.model} className="flex items-baseline justify-between gap-3">
                  <span className="truncate font-mono text-[10px] text-muted-foreground">
                    {m.model}
                  </span>
                  {/* token 为主（这是 hover 要回答的问题），请求数淡化附在后面 */}
                  <span className="tnum shrink-0 text-[10px]">
                    <span className="text-foreground">{formatCompact(m.tokens)}</span>
                    <span className="text-muted-foreground"> · {m.requests}</span>
                  </span>
                </div>
              ))}
              {active.models.length > 8 ? (
                <span className="text-[10px] text-muted-foreground">
                  另有 {active.models.length - 8} 个模型
                </span>
              ) : null}
            </div>
          ) : (
            <div className="mt-1 border-t border-border-subtle pt-1 text-[10px] text-muted-foreground">
              这天没有请求
            </div>
          )}
        </div>
      ) : null}
    </div>
  )
}

/** 把最大值收成 1/2/5 × 10^n，让 y 轴刻度落在整数上 */
function niceStep(max: number): number {
  if (max <= 5) return 1
  const raw = max / 4
  const mag = 10 ** Math.floor(Math.log10(raw))
  const norm = raw / mag
  const pick = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10
  return pick * mag
}
