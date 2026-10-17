/**
 * 概览页（★ 2026-10-16：与「用量」页合并后的唯一一页）。
 *
 * 版面顺序按"排障时的视线路径"排：
 *   横幅（出什么事了）→ 账号 + 签到（是谁、还能不能用、今天领了没）
 *   → 用量（花了多少、花在哪）→ 最近请求（最后发生了什么）
 *
 * ★ 为什么把两个页面合并：
 *   原来的「概览」回答"号还能不能用"，「用量」回答"花了多少"。
 *   可这两件事在排障时**总是一起被问**：余额快见底了，就该顺手看消耗速度；
 *   失败率飙升了，就该顺手看是哪个账号在挨打。分页意味着每次都要切一次视野，
 *   而合并后一屏就能读完这条链路。代价是页面变长，所以把**最近请求放在最底部** ——
 *   它是"追到底"时才看的东西，不该占据首屏。
 *
 * ★ 历史上删掉的东西（不要加回来）：
 *   · 顶部那 6 个"本会话"口径的 KPI：它们自服务启动累计，服务一重启就归零，
 *     既不对应"今天"（那要看用量区块），也无法与任何页面交叉核对。
 *   · 账号与签到的两张表：签到改为服务端全自动后，"勾选框 + 批量 + 二次确认"
 *     这套表单就没有存在意义了；账号本身也按需求收敛成 4 个字段
 *     （账号 / 用户名 / 余额 / 是否签到），不再用表格呈现。
 */

import { Stethoscope, RefreshCw } from 'lucide-react'
import { Page } from '@/components/page'
import { Panel } from '@/components/panel'
import { HealthBanner } from '@/components/health-banner'
import { AccountsCheckinPanel } from '@/components/accounts-checkin-panel'
import { RequestLog } from '@/components/request-log'
import { UsageSection, useUsageWindow } from '@/components/usage-section'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useCheckin, useHealth, useLog, useReload, useSelftest, useStatus } from '@/hooks/use-gateway'

/** 首屏骨架。只盖住会变的部分，不做全屏 spinner —— 全屏转圈会让人以为页面挂了。 */
function OverviewSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      <Skeleton className="h-[74px] rounded-lg" />
      <Skeleton className="h-56 rounded-lg" />
      <Skeleton className="h-[74px] rounded-lg" />
      <Skeleton className="h-52 rounded-lg" />
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
  // 账号行的"余额"tooltip 要显示窗口内的请求数，所以这里与用量区块共用同一个窗口与查询
  // （同一个 queryKey，不会多发一次请求）。
  const { windowMeta, usageByAccount } = useUsageWindow()

  const accounts = status.data?.accounts ?? []
  const firstLoad = status.isLoading && !status.data

  return (
    <Page
      title="概览"
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
            offline={health.isError}
          />

          {/* id="accounts" 是健康横幅「去签到」按钮的跳转目标 ——
              改名时两处必须一起改，否则按钮会静默失效。 */}
          <div id="accounts" className="scroll-mt-16">
            <Panel
              title="账号"
              description="签到由服务端全自动执行；这里只显示身份、余额与今日签到状态"
              flush
            >
              <AccountsCheckinPanel
                accounts={accounts}
                checkin={checkin.data?.accounts ?? []}
                auto={checkin.data?.auto}
                usageByAccount={usageByAccount}
                usageWindowLabel={windowMeta.label}
              />
            </Panel>
          </div>

          <UsageSection />

          <Panel title="最近请求" flush>
            <RequestLog items={log.data?.items ?? []} />
          </Panel>
        </>
      )}
    </Page>
  )
}
