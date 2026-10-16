/**
 * 概览页。
 *
 * 版面顺序是按"排障时的视线路径"排的，不是按数据重要性：
 *   横幅（出什么事了）→ KPI（有多严重）→ 账号表（是谁）→ 签到（怎么补）
 *   → 请求日志（最近发生了什么）
 *
 * 关键取舍：KPI 用一行 inline 统计条，**不是一排彩色大卡片**。
 * 卡片化会把 6 个数字变成 6 个等权重的视觉块，人眼反而找不到优先级；
 * 而且彩色底会把颜色语义稀释掉（颜色在这里专门表示状态）。
 */

import { Stethoscope, RefreshCw } from 'lucide-react'
import { Page } from '@/components/page'
import { Panel } from '@/components/panel'
import { StatStrip, type StatItem } from '@/components/stat-strip'
import { HealthBanner } from '@/components/health-banner'
import { AccountsTable } from '@/components/accounts-table'
import { CheckinPanel } from '@/components/checkin-panel'
import { RequestLog } from '@/components/request-log'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useCheckin, useHealth, useLog, useReload, useSelftest, useStatus } from '@/hooks/use-gateway'
import { formatCredits, formatInt, formatUptime } from '@/lib/format'

/** 首屏骨架。只盖住会变的部分，不做全屏 spinner —— 全屏转圈会让人以为页面挂了。 */
function OverviewSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      <Skeleton className="h-[74px] rounded-lg" />
      <Skeleton className="h-56 rounded-lg" />
      <Skeleton className="h-40 rounded-lg" />
    </div>
  )
}

export function OverviewPage() {
  const health = useHealth()
  const status = useStatus()
  const checkin = useCheckin()
  const log = useLog(80)
  const reload = useReload()
  const selftest = useSelftest()

  const accounts = status.data?.accounts ?? []
  const available = accounts.filter((a) => a.available).length
  const stats = log.data?.stats

  const firstLoad = status.isLoading && !status.data

  const stats_items: StatItem[] = [
    {
      label: '本会话请求',
      value: formatInt(status.data?.session.requests ?? 0),
      hint: '自服务启动',
    },
    {
      label: '本会话消耗',
      value: formatCredits(status.data?.session.credits_spent ?? 0),
      hint: '积分',
    },
    {
      label: '可用账号',
      value: `${available}/${accounts.length}`,
      tone: accounts.length > 0 && available === 0 ? 'danger' : undefined,
    },
    {
      label: '累计失败',
      value: formatInt(stats?.total_errors ?? 0),
      // 0 不着色：常态没有颜色，颜色一出现就必然意味着"看这里"
      tone: (stats?.total_errors ?? 0) > 0 ? 'danger' : undefined,
    },
    {
      label: '运行时长',
      value: formatUptime(stats?.uptime_s),
      hint: stats?.started_at ? `自 ${stats.started_at.slice(5, 16)}` : undefined,
    },
    {
      label: '测试请求',
      value: formatInt(stats?.test_requests ?? 0),
      hint: '不计入用量',
    },
  ]

  return (
    <Page
      title="概览"
      description="账号健康、签到与最近请求。数据每 5 秒自动刷新。"
      actions={
        <>
          <Button
            size="sm"
            variant="outline"
            disabled={selftest.isPending}
            onClick={() => selftest.mutate('deepseek-v4-flash')}
          >
            <Stethoscope />
            {selftest.isPending ? '自检中…' : '自检'}
          </Button>
          <Button size="sm" disabled={reload.isPending} onClick={() => reload.mutate()}>
            <RefreshCw className={reload.isPending ? 'animate-spin' : undefined} />
            {reload.isPending ? '重载中…' : '重载凭据'}
          </Button>
        </>
      }
    >
      {firstLoad ? (
        <OverviewSkeleton />
      ) : (
        <>
          <HealthBanner
            accounts={accounts}
            checkin={checkin.data?.accounts ?? []}
            stats={stats}
            offline={health.isError}
          />

          <StatStrip items={stats_items} />

          <Panel
            title="账号"
            description="凭据状态、余额与本会话用量。点账号 ID 可复制。"
          >
            <AccountsTable accounts={accounts} />
          </Panel>

          <div id="checkin" className="scroll-mt-16">
            <Panel
              title="签到"
              description="半自动：勾选后需二次确认才会真实领取。每个账号每日一次机会。"
            >
              <CheckinPanel accounts={checkin.data?.accounts ?? []} />
            </Panel>
          </div>

          <Panel
            title="最近请求"
            description="最新的 80 条。带「测试」标的回归流量不计入用量统计。"
            flush
          >
            <RequestLog items={log.data?.items ?? []} />
          </Panel>
        </>
      )}
    </Page>
  )
}
