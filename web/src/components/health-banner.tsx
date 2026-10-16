/**
 * 健康横幅。
 *
 * 两条设计原则：
 *  1. **没问题时整块不渲染**。常驻的绿色「一切正常」横幅是纯占位，
 *     几天后用户就会完全无视这个位置 —— 于是真正出问题时也看不见了。
 *  2. **最多报 3 条，按严重度排序**。全量罗列会让"凭据全部失效"和
 *     "一个账号在冷却"看起来一样重要。
 *
 * 严重度顺序：服务不可达 > 凭据失效 > 全部不可用 > 额度耗尽 > 冷却 > 过期临近 > 签到待领
 */

import { AlertTriangle, Clock, Gift, ShieldAlert, WifiOff, type LucideIcon } from 'lucide-react'
import { Link } from 'react-router'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { AccountTone } from '@/lib/format'
import { daysUntil } from '@/lib/format'
import type { Account, CheckinAccount, LogStats } from '@/lib/types'

interface Issue {
  key: string
  tone: AccountTone
  icon: LucideIcon
  title: string
  detail: string
  action?: { label: string; to: string }
}

const TONE_CLASS: Record<AccountTone, string> = {
  danger: 'border-danger-border bg-danger-subtle',
  warning: 'border-warning-border bg-warning-subtle',
  success: 'border-success-border bg-success-subtle',
  idle: 'border-idle-border bg-idle-subtle',
}

const ICON_CLASS: Record<AccountTone, string> = {
  danger: 'text-danger',
  warning: 'text-warning',
  success: 'text-success',
  idle: 'text-muted-foreground',
}

const MAX_ISSUES = 3

function collect(
  accounts: Account[],
  checkin: CheckinAccount[],
  stats: LogStats | undefined,
  offline: boolean,
): Issue[] {
  const issues: Issue[] = []

  if (offline) {
    issues.push({
      key: 'offline',
      tone: 'danger',
      icon: WifiOff,
      title: '连不上网关服务',
      detail: '请确认网关在运行（./run.sh），然后刷新本页。',
    })
    return issues
  }

  const deadCreds = accounts.filter((a) => !a.token_valid && !a.refresh_valid)
  if (deadCreds.length > 0) {
    issues.push({
      key: 'dead-creds',
      tone: 'danger',
      icon: ShieldAlert,
      title: `${deadCreds.length} 个账号凭据已失效`,
      detail: `${deadCreds.map((a) => a.nickname || a.uin).join('、')} —— access 与 refresh token 都不可用，需要在客户端重新登录后重载。`,
    })
  }

  const available = accounts.filter((a) => a.available).length
  if (accounts.length > 0 && available === 0) {
    issues.push({
      key: 'no-account',
      tone: 'danger',
      icon: AlertTriangle,
      title: '当前没有可用账号',
      detail: '所有账号都不可用，新请求会直接失败。先看下面的账号表定位原因。',
    })
  }

  const exhausted = accounts.filter((a) => a.exhausted && a.available === false)
  if (exhausted.length > 0) {
    issues.push({
      key: 'exhausted',
      tone: 'warning',
      icon: AlertTriangle,
      title: `${exhausted.length} 个账号额度耗尽`,
      detail: `${exhausted.map((a) => a.nickname || a.uin).join('、')} —— 去下方「签到」领取今日积分。`,
      action: { label: '去签到', to: '#checkin' },
    })
  }

  // 签到待领：只统计"支持签到 + 查询成功 + 今天还没领"的
  const claimable = checkin.filter(
    (c) => c.supported && !c.error && c.today_checked_in === false,
  )
  if (claimable.length > 0) {
    issues.push({
      key: 'claimable',
      tone: 'warning',
      icon: Gift,
      title: `有 ${claimable.length} 个账号今日签到未领`,
      detail: '签到积分不会自动领取（半自动设计），需要你在下方显式确认。',
      action: { label: '去领取', to: '#checkin' },
    })
  }

  // token 即将过期（7 天内）
  const expiring = accounts.filter((a) => {
    const days = daysUntil(a.expires_at)
    return days !== null && days >= 0 && days <= 7
  })
  if (expiring.length > 0) {
    issues.push({
      key: 'expiring',
      tone: 'warning',
      icon: Clock,
      title: `${expiring.length} 个账号的 token 将在 7 天内过期`,
      detail: '服务每 30 分钟会自动续期一次；若持续不续期，需要在客户端重新登录。',
    })
  }

  if (stats && stats.total_errors > 0) {
    issues.push({
      key: 'errors',
      tone: 'warning',
      icon: AlertTriangle,
      title: `本进程累计 ${stats.total_errors} 次失败请求`,
      detail: '失败详情见下方「最近请求」里标红的行。',
    })
  }

  return issues
}

export function HealthBanner({
  accounts,
  checkin,
  stats,
  offline,
}: {
  accounts: Account[]
  checkin: CheckinAccount[]
  stats: LogStats | undefined
  offline: boolean
}) {
  const issues = collect(accounts, checkin, stats, offline).slice(0, MAX_ISSUES)
  if (issues.length === 0) return null

  return (
    <div className="flex flex-col gap-2">
      {issues.map((issue) => (
        <div
          key={issue.key}
          className={cn(
            'flex items-start gap-2.5 rounded-lg border px-3 py-2.5',
            TONE_CLASS[issue.tone],
          )}
        >
          <issue.icon className={cn('mt-0.5 size-4 shrink-0', ICON_CLASS[issue.tone])} />
          <div className="min-w-0 flex-1">
            <p className="text-xs font-semibold">{issue.title}</p>
            <p className="mt-0.5 text-2xs text-muted-foreground">{issue.detail}</p>
          </div>
          {issue.action?.to.startsWith('#') ? (
            <Button
              size="xs"
              variant="outline"
              className="shrink-0"
              onClick={() => {
                document
                  .getElementById(issue.action!.to.slice(1))
                  ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
              }}
            >
              {issue.action.label}
            </Button>
          ) : issue.action ? (
            <Button size="xs" variant="outline" className="shrink-0" asChild>
              <Link to={issue.action.to}>{issue.action.label}</Link>
            </Button>
          ) : null}
        </div>
      ))}
    </div>
  )
}
