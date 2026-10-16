/**
 * 用量统计页。
 *
 * 服务端把统计分成四个窗口（today / d7 / d30 / all）和三个维度
 * （按日 days、按模型 models、按账号 accounts）。页面结构直接对应这个模型：
 *
 *   窗口选择 → 该窗口的 KPI → 按日走势 → 按模型明细 → 按账号明细
 *
 * 一个必须讲清楚的细节：**回归测试流量单独计数**（test_requests），
 * 不进 totals。所以这里要在页脚明确标出来 —— 否则看到"日志里有请求、
 * 用量里没有"会以为是统计丢了数据。
 */

import { useMemo } from 'react'
import { useSearchParams } from 'react-router'
import { Page } from '@/components/page'
import { Panel } from '@/components/panel'
import { StatStrip, type StatItem } from '@/components/stat-strip'
import { EmptyState } from '@/components/empty-state'
import { UsageChart, type UsageMetric } from '@/components/usage-chart'
import { ClickToCopy } from '@/components/copy-button'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useUsage } from '@/hooks/use-gateway'
import { formatCredits, formatDuration, formatInt, formatPercent } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { UsageAggregate, UsageResponse } from '@/lib/types'

type WindowKey = 'd7' | 'd30' | 'd90'

const WINDOWS: { key: WindowKey; label: string; days: number; totalKey: keyof UsageResponse['totals'] }[] = [
  { key: 'd7', label: '近 7 天', days: 7, totalKey: 'd7' },
  { key: 'd30', label: '近 30 天', days: 30, totalKey: 'd30' },
  { key: 'd90', label: '近 90 天', days: 90, totalKey: 'all' },
]

/** 图表可切换的指标，沿用旧面板的三种 */
const METRIC_OPTIONS: { key: UsageMetric; label: string }[] = [
  { key: 'requests', label: '请求' },
  { key: 'credits', label: '积分' },
  { key: 'tokens', label: 'tokens' },
]

const HEAD = 'h-8 px-3 text-2xs font-medium text-muted-foreground whitespace-nowrap'
const CELL = 'h-8 px-3 py-0 align-middle'

/** 失败率：只有真的失败过才着色，0% 不着色 */
function ErrorRateCell({ rate }: { rate: number }) {
  const text = formatPercent(rate)
  if (!rate || rate <= 0) {
    return <span className="tnum text-xs text-muted-foreground">{text}</span>
  }
  const tone = rate >= 0.1 ? 'text-danger' : 'text-warning'
  return <span className={cn('tnum text-xs font-medium', tone)}>{text}</span>
}

/**
 * 二维聚合表。按模型和按账号的结构完全一样，只是键名不同 ——
 * 抽成一个组件，避免两份会各自漂移的复制粘贴。
 *
 * 用泛型约束键名，而不是 `as Record<string, unknown>` 硬转：
 * 后者会让 TS 失去对 row.model / row.account 的检查，
 * 键名写错了要到运行时才发现。
 */
function AggregateTable<K extends string>({
  rows,
  keyName,
  keyLabel,
  emptyHint,
}: {
  rows: (UsageAggregate & { [P in K]: string })[]
  keyName: K
  keyLabel: string
  emptyHint: string
}) {
  if (rows.length === 0) {
    return <EmptyState title={`还没有按${keyLabel}的数据`} description={emptyHint} />
  }

  return (
    <div className="scroll-thin overflow-x-auto">
      <Table className="text-xs">
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className={HEAD}>{keyLabel}</TableHead>
            <TableHead className={cn(HEAD, 'text-right')}>请求</TableHead>
            <TableHead className={cn(HEAD, 'text-right')}>失败</TableHead>
            <TableHead className={cn(HEAD, 'text-right')}>失败率</TableHead>
            <TableHead className={cn(HEAD, 'text-right')}>积分</TableHead>
            <TableHead className={cn(HEAD, 'text-right')}>tokens</TableHead>
            <TableHead className={cn(HEAD, 'text-right')}>平均延时</TableHead>
            <TableHead className={cn(HEAD, 'text-right')}>最慢</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => {
            const key = String(row[keyName] ?? '—')
            return (
              <TableRow key={key} className="group">
                <TableCell className={cn(CELL, 'max-w-[16rem]')}>
                  <ClickToCopy
                    value={key}
                    label={`复制 ${keyLabel} ${key}`}
                    className="font-mono text-2xs text-foreground"
                  />
                </TableCell>
                <TableCell className={cn(CELL, 'text-right')}>
                  <span className="tnum text-xs font-medium">{formatInt(row.requests)}</span>
                </TableCell>
                <TableCell className={cn(CELL, 'text-right')}>
                  <span
                    className={cn(
                      'tnum text-xs',
                      row.errors > 0 ? 'font-medium text-danger' : 'text-muted-foreground',
                    )}
                  >
                    {formatInt(row.errors)}
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
                <TableCell className={cn(CELL, 'text-right')}>
                  <span className="tnum text-xs text-muted-foreground">{formatDuration(row.avg_ms)}</span>
                </TableCell>
                <TableCell className={cn(CELL, 'text-right')}>
                  <span className="tnum text-xs text-muted-foreground">{formatDuration(row.max_ms)}</span>
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

export function UsagePage() {
  const [params, setParams] = useSearchParams()
  const windowKey = (params.get('w') as WindowKey | null) ?? 'd7'
  const windowMeta = WINDOWS.find((w) => w.key === windowKey) ?? WINDOWS[0]
  const metric = (params.get('m') as UsageMetric | null) ?? 'requests'

  /** 只改一个参数，其余保持 */
  const patch = (next: Record<string, string | null>): void => {
    const merged = new URLSearchParams(params)
    for (const [key, value] of Object.entries(next)) {
      if (value === null || value === '') merged.delete(key)
      else merged.set(key, value)
    }
    setParams(merged, { replace: true })
  }

  const usage = useUsage(windowMeta.days)
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
      { label: '平均延时', value: formatDuration(totals.avg_ms) },
    ]
  }, [totals, windowMeta.label])

  const firstLoad = usage.isLoading && !data

  return (
    <Page
      title="用量"
      description={
        data
          ? `统计窗口内共 ${formatInt(totals?.requests ?? 0)} 次请求。数据保留 ${data.retention_days} 天，${data.persisted ? '已落盘' : '仅内存'}。`
          : '正在读取用量统计…'
      }
      actions={
        <div
          role="group"
          aria-label="统计窗口"
          className="flex items-center gap-0.5 rounded-md bg-muted p-0.5"
        >
          {WINDOWS.map((item) => {
            const active = item.key === windowMeta.key
            return (
              <Button
                key={item.key}
                size="xs"
                variant={active ? 'secondary' : 'ghost'}
                aria-pressed={active}
                onClick={() => patch({ w: item.key === 'd7' ? null : item.key })}
                className={cn('h-6 px-2 text-2xs', active && 'shadow-border')}
              >
                {item.label}
              </Button>
            )
          })}
        </div>
      }
    >
      {firstLoad ? (
        <div className="flex flex-col gap-4">
          <Skeleton className="h-[74px] rounded-lg" />
          <Skeleton className="h-[260px] rounded-lg" />
          <Skeleton className="h-52 rounded-lg" />
        </div>
      ) : (
        <>
          <StatStrip items={statItems} />

          <Panel
            title="每日走势"
            description={`按日聚合，共 ${data?.days.length ?? 0} 天（含无流量的 0 值日，保证时间轴连续）。`}
            actions={
              <div
                role="group"
                aria-label="图表指标"
                className="flex items-center gap-0.5 rounded-md bg-muted p-0.5"
              >
                {METRIC_OPTIONS.map((item) => {
                  const active = item.key === metric
                  return (
                    <Button
                      key={item.key}
                      size="xs"
                      variant={active ? 'secondary' : 'ghost'}
                      aria-pressed={active}
                      onClick={() => patch({ m: item.key === 'requests' ? null : item.key })}
                      className={cn('h-6 px-2 text-2xs', active && 'shadow-border')}
                    >
                      {item.label}
                    </Button>
                  )
                })}
              </div>
            }
          >
            <UsageChart days={data?.days ?? []} metric={metric} />
          </Panel>

          <Panel title="按模型" description="按请求次数排序。" flush>
            <AggregateTable
              rows={data?.models ?? []}
              keyName="model"
              keyLabel="模型"
              emptyHint="还没有产生过请求。用「概览」页的自检发一条试试，或按「接入」页配置好客户端。"
            />
          </Panel>

          <Panel title="按账号" description="用于发现某个账号是否被过度使用或频繁失败。" flush>
            <AggregateTable
              rows={data?.accounts ?? []}
              keyName="account"
              keyLabel="账号"
              emptyHint="还没有产生过请求。"
            />
          </Panel>

          {/* 记账口径说明：不写清楚，日志与用量对不上时会被误判为丢数据 */}
          {data ? (
            <p className="px-1 text-2xs text-muted-foreground">
              回归测试流量 <span className="tnum">{formatInt(data.test_requests)}</span> 次单独计数，
              <b className="font-medium text-foreground">不计入</b>上面的统计
              （它们带 <code className="font-mono">X-Gateway-Test</code> 头）。
              数据文件：<code className="scroll-thin font-mono">{data.path}</code>
              {data.loaded_records > 0 ? (
                <>
                  ，本次已载入 <span className="tnum">{formatInt(data.loaded_records)}</span> 条历史记录
                </>
              ) : null}
              {data.first_at ? <>，最早一条 <span className="tnum">{data.first_at}</span></> : null}
            </p>
          ) : null}
        </>
      )}
    </Page>
  )
}
