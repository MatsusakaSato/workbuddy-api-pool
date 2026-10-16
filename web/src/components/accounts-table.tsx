/**
 * 账号表。
 *
 * 密集表格的几条硬规矩（做不对就会显得业余）：
 *  · **行高 32px**（h-8），单元格 padding 用 0 + px-3，垂直居中靠行高 ——
 *    不要用 py 堆，否则行高会随内容漂移；
 *  · **数字列右对齐 + tnum**，否则 1831 和 999 的左边界不对齐，逐行参差；
 *  · 文本列给 max-width + truncate，长昵称不许把表格撑破；
 *  · 表头 12px / 500 / 次级灰，**不做全大写**（中文无大小写，英文全大写会丢失
 *    小写字母的轮廓特征，反而更难读）；
 *  · 状态用圆点 + 文字，不用彩色 pill —— 一行里出现多个彩色块就是 Badge 汤。
 */

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { StatusLabel } from '@/components/status-dot'
import { ClickToCopy } from '@/components/copy-button'
import { EmptyState } from '@/components/empty-state'
import {
  accountState,
  daysUntil,
  formatCredits,
  formatDuration,
  formatInt,
  regionLabel,
} from '@/lib/format'
import { cn } from '@/lib/utils'
import type { Account } from '@/lib/types'

const HEAD = 'h-8 px-3 text-2xs font-medium text-muted-foreground whitespace-nowrap'
const CELL = 'h-8 px-3 py-0 align-middle'

/** token 到期：把"还有几天"做成有色调的短标签，绝对时间放 tooltip */
function ExpiryCell({ at }: { at: string }) {
  const days = daysUntil(at)
  if (days === null) return <span className="text-muted-foreground">—</span>

  const tone = days < 0 ? 'danger' : days <= 7 ? 'warning' : 'success'
  const text = days < 0 ? '已过期' : days === 0 ? '今天' : `${days}d`

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-block cursor-default">
          <StatusLabel tone={tone}>{text}</StatusLabel>
        </span>
      </TooltipTrigger>
      <TooltipContent className="tnum">{at}</TooltipContent>
    </Tooltip>
  )
}

function AccountRow({ account }: { account: Account }) {
  const state = accountState(account)

  return (
    <TableRow className="group">
      {/* 账号：昵称 + 内部 ID。整格可点复制 ID，因为排障时最常做的就是把 uin 拿去 grep */}
      <TableCell className={cn(CELL, 'max-w-[16rem]')}>
        <ClickToCopy
          value={account.uin}
          label={`复制账号 ID ${account.uin}`}
          className="flex-col items-start gap-0 hover:text-foreground"
        >
          <span className="flex w-full min-w-0 items-baseline gap-1.5">
            <span className="truncate text-xs font-medium">{account.nickname || '（无昵称）'}</span>
            <span className="shrink-0 rounded-sm bg-muted px-1 text-2xs text-muted-foreground">
              {regionLabel(account.region)}
            </span>
          </span>
          <span className="tnum w-full truncate font-mono text-2xs text-muted-foreground">
            {account.uin}
          </span>
        </ClickToCopy>
      </TableCell>

      {/* 状态：把一堆布尔压成一个结论；细节放 tooltip */}
      <TableCell className={cn(CELL, 'whitespace-nowrap')}>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="cursor-default">
              <StatusLabel tone={state.tone}>{state.label}</StatusLabel>
            </span>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs">
            {state.detail ?? '一切正常'}
            {account.last_error && state.detail !== account.last_error ? (
              <div className="mt-1 font-mono text-2xs opacity-80">{account.last_error}</div>
            ) : null}
          </TooltipContent>
        </Tooltip>
      </TableCell>

      {/* 余额：可能为 null（还没拉到），必须区分"未知"和"0" */}
      <TableCell className={cn(CELL, 'text-right')}>
        {account.credits === null ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="cursor-default text-muted-foreground">—</span>
            </TooltipTrigger>
            <TooltipContent>尚未拉到余额，点右上「重载凭据」获取</TooltipContent>
          </Tooltip>
        ) : (
          <span className="tnum text-xs font-medium">{formatCredits(account.credits)}</span>
        )}
      </TableCell>

      <TableCell className={cn(CELL, 'text-right')}>
        <span className="tnum text-xs text-muted-foreground">
          {formatInt(account.requests_session)}
        </span>
      </TableCell>

      <TableCell className={cn(CELL, 'text-right')}>
        <span className="tnum text-xs text-muted-foreground">
          {formatCredits(account.credits_spent_session)}
        </span>
      </TableCell>

      {/* 上游延迟不在这里展示（属请求维度），只有续期状态放这列 */}
      <TableCell className={cn(CELL, 'whitespace-nowrap')}>
        <ExpiryCell at={account.expires_at} />
      </TableCell>

      <TableCell className={cn(CELL, 'whitespace-nowrap text-right')}>
        <span className="tnum text-2xs text-muted-foreground">
          {formatDuration(account.cooldown_remaining_s * 1000)}
        </span>
      </TableCell>
    </TableRow>
  )
}

export function AccountsTable({ accounts }: { accounts: Account[] }) {
  if (accounts.length === 0) {
    return (
      <EmptyState
        title="没有读到任何账号"
        description="凭据是从桌面客户端的登录态里读出来的。确认客户端已登录，然后点「重载凭据」。"
        action={<span className="text-2xs text-muted-foreground">用右上角的重载按钮重试</span>}
      />
    )
  }

  const totalCredits = accounts.reduce((sum, a) => sum + (a.credits ?? 0), 0)

  return (
    <div className="scroll-thin overflow-x-auto">
      <Table className="text-xs">
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className={HEAD}>账号</TableHead>
            <TableHead className={HEAD}>状态</TableHead>
            <TableHead className={cn(HEAD, 'text-right')}>余额</TableHead>
            <TableHead className={cn(HEAD, 'text-right')}>本会话请求</TableHead>
            <TableHead className={cn(HEAD, 'text-right')}>本会话消耗</TableHead>
            <TableHead className={HEAD}>Token 到期</TableHead>
            <TableHead className={cn(HEAD, 'text-right')}>冷却</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {accounts.map((account) => (
            <AccountRow key={account.account_id} account={account} />
          ))}
        </TableBody>
      </Table>

      {/* 合计行：只在多账号时才有意义 */}
      {accounts.length > 1 ? (
        <div className="flex items-center justify-end gap-4 border-t border-border-subtle px-3 py-1.5 text-2xs text-muted-foreground">
          <span>
            合计余额{' '}
            <span className="tnum font-medium text-foreground">{formatCredits(totalCredits)}</span>
          </span>
        </div>
      ) : null}
    </div>
  )
}
