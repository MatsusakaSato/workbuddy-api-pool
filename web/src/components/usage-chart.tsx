/**
 * 按日用量柱状图。
 *
 * 几条刻意的克制：
 *  · **主力序列只有一种颜色**（交互蓝）。图表里每多一种颜色，
 *    就多一分"这颜色什么意思"的困惑。不搞彩虹。
 *  · **失败序列只在真的有失败时才渲染**，且只在指标是「请求」时出现 ——
 *    把"失败次数"和"积分"画在同一根 Y 轴上没有意义（量纲不同）。
 *  · 网格只画横线（`vertical={false}`）。纵向网格线在柱子之间没有参考价值，
 *    只会让图变脏。
 *  · Y 轴宽度固定，否则数值位数变化时绘图区会左右抖动。
 */

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { formatCompact, formatCredits, formatInt } from '@/lib/format'
import type { UsageDay } from '@/lib/types'

/** 可切换的统计指标。与旧面板的「请求 / 积分 / tokens」一致。 */
export type UsageMetric = 'requests' | 'credits' | 'tokens'

interface MetricSpec {
  label: string
  /** Y 轴刻度：必须极简，坐标轴上放不下 "1.2k tokens" */
  axis: (value: number) => string
  /** tooltip 里的精确值 */
  exact: (value: number) => string
}

const METRICS: Record<UsageMetric, MetricSpec> = {
  requests: { label: '请求', axis: formatCompact, exact: (v) => formatInt(v) },
  credits: {
    label: '积分',
    // 积分数量级很小（0.05 一次），用 k/M 压缩会把它们全变成 0
    axis: (v) => (v >= 100 ? formatCompact(v) : v.toFixed(v < 1 ? 2 : 1)),
    exact: (v) => formatCredits(v),
  },
  tokens: { label: 'tokens', axis: formatCompact, exact: (v) => formatInt(v) },
}

/** "2026-10-11" → "10/11" */
function shortDate(iso: string): string {
  const parts = iso.split('-')
  return parts.length === 3 ? `${Number(parts[1])}/${Number(parts[2])}` : iso
}

interface TooltipPayloadItem {
  dataKey?: string | number
  value?: number | string
  color?: string
}

function ChartTooltip({
  active,
  payload,
  label,
  metric,
}: {
  active?: boolean
  payload?: TooltipPayloadItem[]
  label?: string | number
  metric: UsageMetric
}) {
  if (!active || !payload?.length) return null
  const spec = METRICS[metric]
  return (
    <div className="rounded-md bg-popover px-2.5 py-1.5 shadow-border">
      <p className="tnum text-2xs font-medium text-foreground">{String(label)}</p>
      <ul className="mt-1 space-y-0.5">
        {payload.map((item) => (
          <li key={String(item.dataKey)} className="flex items-center gap-2 text-2xs">
            <span
              aria-hidden
              className="size-1.5 shrink-0 rounded-full"
              style={{ background: item.color }}
            />
            <span className="text-muted-foreground">
              {item.dataKey === 'errors' ? '失败' : spec.label}
            </span>
            <span className="tnum ml-auto font-medium text-foreground">
              {item.dataKey === 'errors'
                ? formatInt(Number(item.value))
                : spec.exact(Number(item.value))}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function UsageChart({ days, metric }: { days: UsageDay[]; metric: UsageMetric }) {
  const spec = METRICS[metric]
  const totalErrors = days.reduce((sum, d) => sum + d.errors, 0)
  // 失败次数只有在画"请求"时才作为第二序列出现
  const showErrors = metric === 'requests' && totalErrors > 0

  // 空状态要**按指标分别判断**：很可能有请求但没积分消耗
  // （比如全是自检流量，上游记 0 积分）。这时柱高全是 0，recharts 一个矩形都不画，
  // 屏幕上只剩一张空白坐标系 —— 不解释的话看起来就是坏了。
  const hasData = days.some((d) => (d[metric] ?? 0) > 0)

  if (!hasData) {
    return (
      <div className="flex h-[180px] items-center justify-center px-6">
        <p className="text-center text-2xs text-muted-foreground">
          {metric === 'requests'
            ? '这段时间内还没有请求 —— 有流量后这里会出现每日柱状图'
            : `这段时间内没有${spec.label}消耗（柱高全为 0，所以没有柱子）—— 自检与探针流量通常不扣积分`}
        </p>
      </div>
    )
  }

  return (
    <div className="h-[180px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={days} margin={{ top: 4, right: 4, bottom: 0, left: 0 }} barGap={1}>
          <CartesianGrid vertical={false} stroke="var(--color-border-subtle)" />
          <XAxis
            dataKey="date"
            tickFormatter={shortDate}
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 12, fill: 'var(--color-muted-foreground)' }}
            interval="preserveStartEnd"
            minTickGap={16}
          />
          <YAxis
            width={48}
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 12, fill: 'var(--color-muted-foreground)' }}
            tickFormatter={(v: number) => spec.axis(v)}
          />
          <Tooltip
            content={<ChartTooltip metric={metric} />}
            cursor={{ fill: 'var(--color-muted)', opacity: 0.5 }}
          />
          <Bar
            dataKey={metric}
            name={spec.label}
            fill="var(--color-chart-1)"
            radius={[2, 2, 0, 0]}
            maxBarSize={28}
          />
          {showErrors ? (
            <Bar
              dataKey="errors"
              name="失败"
              fill="var(--color-danger-solid)"
              radius={[2, 2, 0, 0]}
              maxBarSize={28}
            />
          ) : null}
          {showErrors ? (
            <Legend
              verticalAlign="top"
              align="right"
              height={20}
              iconType="circle"
              iconSize={6}
              wrapperStyle={{ fontSize: 12, color: 'var(--color-muted-foreground)' }}
            />
          ) : null}
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
