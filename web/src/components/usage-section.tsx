/**
 * 用量区块。
 *
 * ★ 2026-10-16：从**独立页面**（routes/usage.tsx）变成概览页里的一个区块。
 *   合并的理由：两个页面回答的是同一件事的两半 ——
 *   "号还能不能用"（账号 + 签到）与"这段时间花了多少、花在哪"（用量）。
 *   分成两页时，看余额要切页、看消耗又要切回来。合并后一屏读完整条链路。
 *
 * 区块内部结构与服务端的统计模型一一对应：
 *   窗口选择 → 该窗口的 KPI → 每日走势 → 按模型明细
 *
 * 几个保留的决定（都来自 docs/可视化方案调研.md 的实测）：
 *  1. **保留表格**。「按模型」要逐行比对请求数 / 失败率 / 延迟分布，
 *     列对齐是它不可替代的价值；"去表格"只针对恒定值多、需要形状的账号面板。
 *  2. **延迟用分布条而不是"平均 + 最慢"两列**。两个数字并列时读者无法判断
 *     "慢是常态还是个别事故"（实测比值 1.0×–14.6×）。
 *  3. **行尾加迷你趋势条**：表格每行只能表达"当前值"，趋势条表达"在变多还是变少"，
 *     而行高不变。
 *  4. **删掉了「按账号」聚合表**。账号的展示已按需求收敛成"账号 / 用户名 /
 *     余额 / 是否签到"四项（见 accounts-checkin-panel.tsx）；再放一张按账号的请求表
 *     就是同一批 uin 的第二种呈现。窗口内的每账号请求数没有丢 ——
 *     它挂在账号行「余额」的 tooltip 上（用 uin 关联，见 usageByAccount）。
 *
 * 一个必须讲清楚的细节：**回归测试流量单独计数**（test_requests），
 * 不进 totals。否则看到"日志里有请求、用量里没有"会以为是统计丢了数据。
 */

import { useMemo } from 'react'
import { useSearchParams } from 'react-router'
import { Panel } from '@/components/panel'
import { StatStrip, type StatItem } from '@/components/stat-strip'
import { EmptyState } from '@/components/empty-state'
import {
  DailyTrendChart,
  DistributionLegend,
  LatencyDistribution,
  Sparkline,
  TrendLegend,
} from '@/components/viz'
import { ClickToCopy } from '@/components/copy-button'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useUsage } from '@/hooks/use-gateway'
import { formatCredits, formatDuration, formatInt, formatPercent } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { UsageAccount, UsageModel, UsageResponse } from '@/lib/types'

type WindowKey = 'd1' | 'd7' | 'd30' | 'd90'

/**
 * 统计窗口。
 *
 * 「今天」的取舍：数据口径是**当天自然日**（KPI 与明细表都只看今天），
 * 但**图表仍画完整窗口**——见下面 CHART_MIN_DAYS 的说明。
 * 这是刻意的：把 1 天的数据画成 1 格日历或 1 根柱子，
 * 看起来像渲染坏了，读者反而得不到信息。
 */
const WINDOWS: { key: WindowKey; label: string; days: number; totalKey: keyof UsageResponse['totals'] }[] = [
  { key: 'd1', label: '今天', days: 1, totalKey: 'd1' },
  { key: 'd7', label: '近 7 天', days: 7, totalKey: 'd7' },
  { key: 'd30', label: '近 30 天', days: 30, totalKey: 'd30' },
  { key: 'd90', label: '近 90 天', days: 90, totalKey: 'all' },
]

/**
 * 图表始终至少展示这么多天的上下文。
 *
 * 为什么不让图表跟窗口走：选「今天」时若只画 1 天，柱子只有一根、折线退化成一个点
 * —— 那不是信息，是故障观感。所以窗口只决定**统计口径**，图表永远给足上下文。
 */
const CHART_MIN_DAYS = 30

const HEAD = 'h-8 px-3 text-2xs font-medium text-muted-foreground whitespace-nowrap'
const CELL = 'h-8 px-3 py-0 align-middle'

/**
 * 统计窗口 + 用量数据。
 *
 * 抽成 hook 是为了让概览页的**账号行 tooltip** 也能拿到同一份数据 ——
 * 用同一个 queryKey，两个调用点只会发一次请求。
 * 窗口参数存在 hash 的 `w` 上，所以切窗口是可分享、可刷新的。
 */
export function useUsageWindow() {
  const [params, setParams] = useSearchParams()
  const windowKey = (params.get('w') as WindowKey | null) ?? 'd7'
  // 兜底到 WINDOWS[1]（近 7 天）而不是 [0]：URL 里写了非法 w 时，
  // 落到默认窗口比落到"今天"更符合预期（默认值就是 d7）。
  const windowMeta = WINDOWS.find((w) => w.key === windowKey) ?? WINDOWS[1]

  /** 只改一个参数，其余保持 */
  const patch = (next: Record<string, string | null>): void => {
    const merged = new URLSearchParams(params)
    for (const [key, value] of Object.entries(next)) {
      if (value === null || value === '') merged.delete(key)
      else merged.set(key, value)
    }
    setParams(merged, { replace: true })
  }

  // 图表请求的天数可以比统计窗口大（见 CHART_MIN_DAYS 的说明）。
  // 用 max 而不是固定值：选 90 天时图表自然也应该给 90 天。
  const chartDays = Math.max(windowMeta.days, CHART_MIN_DAYS)
  const usage = useUsage(chartDays)

  /** 窗口内按账号聚合，键是 uin —— 账号面板的 tooltip 靠它关联 */
  const usageByAccount = useMemo(() => {
    const map = new Map<string, UsageAccount>()
    for (const row of usage.data?.accounts ?? []) map.set(row.account, row)
    return map
  }, [usage.data])

  const chartSlice = useMemo(() => {
    const all = usage.data?.days ?? []
    return all.slice(-Math.max(windowMeta.days, CHART_MIN_DAYS))
  }, [usage.data?.days, windowMeta.days])

  return { params, patch, windowMeta, usage, usageByAccount, chartSlice }
}

/** 失败率：只有真的失败过才着色，0% 不着色 */
function ErrorRateCell({ rate }: { rate: number }) {
  const text = formatPercent(rate)
  if (!rate || rate <= 0) {
    return <span className="tnum text-xs text-muted-foreground">{text}</span>
  }
  const tone = rate >= 0.1 ? 'text-danger' : 'text-warning'
  return <span className={cn('tnum text-xs font-medium', tone)}>{text}</span>
}

/** 按模型的聚合表。唯一的明细表，所以不再做泛型化（旧版还有一个"按账号"调用点） */
function ModelAggregateTable({ rows }: { rows: UsageModel[] }) {
  if (rows.length === 0) {
    return (
      <EmptyState
        title="还没有按模型的数据"
        description="还没有产生过请求。用「概览」页的自检发一条试试，或按「接入」页配置好客户端。"
      />
    )
  }

  // 条长按"窗口内请求数最大值"归一 —— 组内归一才能让相对关系可见
  const maxRequests = Math.max(...rows.map((r) => r.requests), 0)

  return (
    <div className="scroll-thin overflow-x-auto">
      <Table className="text-xs">
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className={cn(HEAD, 'max-w-[14rem]')}>模型</TableHead>
            <TableHead className={cn(HEAD, 'w-[4.5rem] text-right')}>请求</TableHead>
            <TableHead className={cn(HEAD, 'w-[6rem]')} title="长度按窗口内最大请求数归一">
              占比
            </TableHead>
            <TableHead className={cn(HEAD, 'w-[4.5rem] text-right')}>失败率</TableHead>
            <TableHead className={cn(HEAD, 'w-[4.5rem] text-right')}>积分</TableHead>
            <TableHead className={cn(HEAD, 'w-[5.5rem] text-right')}>tokens</TableHead>
            <TableHead className={cn(HEAD, 'w-[4.5rem] text-right')}>平均</TableHead>
            <TableHead className={cn(HEAD, 'w-[9rem]')}>延迟分布</TableHead>
            <TableHead className={cn(HEAD, 'w-[5rem]')}>趋势</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => {
            const key = String(row.model ?? '—')
            return (
              <TableRow key={key} className="group">
                <TableCell className={cn(CELL, 'max-w-[16rem]')}>
                  <ClickToCopy
                    value={key}
                    label={`复制模型 ${key}`}
                    className="font-mono text-2xs text-foreground"
                  />
                </TableCell>

                <TableCell className={cn(CELL, 'text-right')}>
                  <span className="tnum text-xs font-medium">{formatInt(row.requests)}</span>
                </TableCell>

                {/* 占比条：把"请求数"这个数字变成可比长度。最长的行满格，一眼看出谁是大头 */}
                <TableCell className={CELL}>
                  <span className="block h-3.5 w-full overflow-hidden rounded-sm bg-muted">
                    <span
                      className="block h-full rounded-sm bg-chart-gradient-2"
                      style={{ width: `${maxRequests > 0 ? (row.requests / maxRequests) * 100 : 0}%` }}
                    />
                  </span>
                </TableCell>

                <TableCell className={cn(CELL, 'text-right')}>
                  <ErrorRateCell rate={row.error_rate} />
                </TableCell>

                <TableCell className={cn(CELL, 'text-right')}>
                  <span className="tnum text-xs">{formatCredits(row.credits)}</span>
                </TableCell>

                <TableCell className={cn(CELL, 'text-right')}>
                  <span className="tnum text-xs text-muted-foreground">{formatInt(row.tokens)}</span>
                </TableCell>

                {/* 「平均」保留了原样：它是最常被引用的单个数字。
                    但「最慢」被移进了分布条的 tooltip —— 它单独成列时
                    会和平均值互相稀释，放在分布形状旁边才有参照意义。 */}
                <TableCell className={cn(CELL, 'text-right')}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="tnum cursor-default text-xs text-muted-foreground">
                        {formatDuration(row.avg_ms)}
                      </span>
                    </TooltipTrigger>
                    <TooltipContent className="tnum">
                      p50 {formatDuration(row.p50_ms)} · p90 {formatDuration(row.p90_ms)} · p99{' '}
                      {formatDuration(row.p99_ms)}
                      <br />
                      最慢 {formatDuration(row.max_ms)} · 共 {formatInt(row.requests)} 次
                    </TooltipContent>
                  </Tooltip>
                </TableCell>

                <TableCell className={CELL}>
                  <LatencyDistribution
                    p50={row.p50_ms}
                    p90={row.p90_ms}
                    p99={row.p99_ms}
                    max={row.max_ms}
                    requests={row.requests}
                  />
                </TableCell>

                <TableCell className={CELL}>
                  <Sparkline values={row.daily ?? []} />
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

/** 窗口切换：分段控件，选中态用品牌蓝实心（与模型页的分面控件保持一致） */
function WindowSwitch({
  active,
  onChange,
}: {
  active: WindowKey
  onChange: (key: WindowKey) => void
}) {
  return (
    <div role="group" aria-label="统计窗口" className="flex items-center gap-0.5 rounded-md bg-muted p-0.5">
      {WINDOWS.map((item) => {
        const on = item.key === active
        return (
          <Button
            key={item.key}
            size="xs"
            variant={on ? 'secondary' : 'ghost'}
            aria-pressed={on}
            onClick={() => onChange(item.key)}
            className={cn(
              'h-6 px-2 text-2xs',
              on && 'bg-primary text-primary-foreground shadow-primary hover:bg-primary',
            )}
          >
            {item.label}
          </Button>
        )
      })}
    </div>
  )
}

export function UsageSection() {
  const { patch, windowMeta, usage, chartSlice } = useUsageWindow()
  const data = usage.data
  const totals = data?.totals[windowMeta.totalKey]

  const statItems: StatItem[] = useMemo(() => {
    if (!totals) return []
    return [
      { label: '请求', value: formatInt(totals.requests), hint: windowMeta.label },
      {
        label: '失败',
        value: formatInt(totals.errors),
        tone: totals.errors > 0 ? 'danger' : undefined,
      },
      { label: '失败率', value: formatPercent(totals.error_rate) },
      { label: '消耗积分', value: formatCredits(totals.credits) },
      { label: 'tokens', value: formatInt(totals.tokens) },
      // p50 比平均值更能代表"典型体验"：平均值会被少量长尾拉高。
      // 实测里 glm-5.3-flash 平均 9.8s、p50 却能低不少。
      { label: '中位延时', value: formatDuration(totals.p50_ms), hint: 'p50' },
    ]
  }, [totals, windowMeta.label])

  const firstLoad = usage.isLoading && !data

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* 用 h2（页面有一个 h1）：用量是概览页里的一个分区，不是子卡片。
            刻意不套 Panel —— 里面的 KPI 条与表格各自已经是卡片，再套一层就是嵌套卡片。 */}
        <h2 className="flex items-center gap-2 text-sm font-semibold tracking-tight">
          <span aria-hidden className="h-3 w-0.5 shrink-0 rounded-full bg-primary" />
          用量
        </h2>
        {firstLoad ? null : (
          <WindowSwitch
            active={windowMeta.key}
            onChange={(key) => patch({ w: key === 'd7' ? null : key })}
          />
        )}
      </div>

      {firstLoad ? (
        <div className="flex flex-col gap-4">
          <Skeleton className="h-[74px] rounded-lg" />
          <Skeleton className="h-[132px] rounded-lg" />
          <Skeleton className="h-52 rounded-lg" />
        </div>
      ) : (
        <>
          <StatStrip items={statItems} />

          <Panel title="每日走势" actions={<TrendLegend />}>
            <DailyTrendChart days={chartSlice} />
          </Panel>

          <Panel title="按模型" actions={<DistributionLegend />} flush>
            <ModelAggregateTable rows={data?.models ?? []} />
          </Panel>
        </>
      )}
    </>
  )
}
