/**
 * 签到面板。
 *
 * **半自动设计的界面体现**：服务端要求必须显式给出 account_ids 才会真领，
 * 空请求直接 400。所以这里的交互必须做到：
 *   1. 默认不勾选任何账号 —— 不提供"一键领全部"的默认路径；
 *   2. 提供「全选未领取」这个**显式**的批量动作，并说明它要做什么；
 *   3. 真正提交前走一次二次确认，把"要领哪几个、会得到什么"讲清楚。
 *
 * 为什么要这么麻烦：签到是真的会调用上游、真的消耗掉每日一次的领取机会。
 * 误点一次的代价无法撤销，所以多一次确认是值得的。
 *
 * checkin 的数据是**三态联合**（不支持 / 查询失败 / 正常），
 * 三种形状的字段完全不同，渲染时必须分别处理，不能假定字段都在。
 */

import { useMemo, useState } from 'react'
import { Flame, Gift } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { StatusLabel } from '@/components/status-dot'
import { EmptyState } from '@/components/empty-state'
import { useClaimCheckin } from '@/hooks/use-gateway'
import { formatCredits } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { CheckinAccount } from '@/lib/types'

const HEAD = 'h-8 px-3 text-2xs font-medium text-muted-foreground whitespace-nowrap'
const CELL = 'h-8 px-0 py-0 align-middle'

/** 能领的账号：支持签到、查询没出错、且今天还没领 */
function isClaimable(row: CheckinAccount): boolean {
  return row.supported && !row.error && row.today_checked_in === false
}

export function CheckinPanel({ accounts }: { accounts: CheckinAccount[] }) {
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [confirmOpen, setConfirmOpen] = useState(false)
  const claim = useClaimCheckin()

  const claimable = useMemo(() => accounts.filter(isClaimable), [accounts])
  const claimableIds = useMemo(() => claimable.map((r) => r.account_id), [claimable])

  // 勾选状态要跟着数据走：领成功后再刷新，那些账号就不该继续被选中
  const effectiveSelection = useMemo(
    () => claimableIds.filter((id) => selected.has(id)),
    [claimableIds, selected],
  )

  if (accounts.length === 0) {
    return (
      <EmptyState
        title="没有读到签到信息"
        description="通常是凭据不可用导致查询失败。先解决凭据问题，再回来看签到。"
      />
    )
  }

  const toggle = (id: string, next: boolean): void => {
    setSelected((prev) => {
      const copy = new Set(prev)
      if (next) copy.add(id)
      else copy.delete(id)
      return copy
    })
  }

  const selectedRows = claimable.filter((r) => effectiveSelection.includes(r.account_id))
  const totalSelectedCredit = selectedRows.reduce((sum, r) => sum + (r.today_credit ?? 0), 0)

  return (
    <>
      <div className="scroll-thin overflow-x-auto">
        <Table className="text-xs">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className={cn(HEAD, 'w-9 pr-0')}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span>
                      <Checkbox
                        aria-label="全选未领取的账号"
                        disabled={claimable.length === 0}
                        checked={
                          claimable.length > 0 && effectiveSelection.length === claimable.length
                            ? true
                            : effectiveSelection.length > 0
                              ? 'indeterminate'
                              : false
                        }
                        onCheckedChange={(next) => {
                          setSelected(next === true ? new Set(claimableIds) : new Set())
                        }}
                      />
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>
                    {claimable.length > 0
                      ? `全选 ${claimable.length} 个今日未领取的账号`
                      : '今天没有可领取的账号'}
                  </TooltipContent>
                </Tooltip>
              </TableHead>
              <TableHead className={HEAD}>账号</TableHead>
              <TableHead className={HEAD}>签到状态</TableHead>
              <TableHead className={cn(HEAD, 'text-right')}>连续</TableHead>
              <TableHead className={cn(HEAD, 'text-right')}>今日 / 每日</TableHead>
              <TableHead className={cn(HEAD, 'text-right')}>累计</TableHead>
              <TableHead className={HEAD}>活动</TableHead>
              <TableHead className={HEAD}>截止</TableHead>
            </TableRow>
          </TableHeader>

          <TableBody>
            {accounts.map((row) => {
              const claimableRow = isClaimable(row)
              const checked = effectiveSelection.includes(row.account_id)

              return (
                <TableRow key={row.account_id} className="group">
                  <TableCell className={cn(CELL, 'w-9 pl-3 pr-0')}>
                    <Checkbox
                      aria-label={`选择账号 ${row.uin}`}
                      disabled={!claimableRow}
                      checked={checked}
                      onCheckedChange={(next) => toggle(row.account_id, next === true)}
                    />
                  </TableCell>

                  <TableCell className={cn(CELL, 'px-3')}>
                    <div className="flex flex-col leading-tight">
                      <span className="text-xs">{row.uin}</span>
                      <span className="text-2xs text-muted-foreground">
                        {row.region === 'cn' ? '国内' : row.region}
                      </span>
                    </div>
                  </TableCell>

                  <TableCell className={cn(CELL, 'whitespace-nowrap px-3')}>
                    {!row.supported ? (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="cursor-default">
                            <StatusLabel tone="idle">不支持</StatusLabel>
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>{row.note ?? '该区域没有签到活动'}</TooltipContent>
                      </Tooltip>
                    ) : row.error ? (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="cursor-default">
                            <StatusLabel tone="danger">查询失败</StatusLabel>
                          </span>
                        </TooltipTrigger>
                        <TooltipContent className="max-w-xs">{row.error}</TooltipContent>
                      </Tooltip>
                    ) : row.today_checked_in ? (
                      <StatusLabel tone="success">今日已领</StatusLabel>
                    ) : (
                      <StatusLabel tone="warning">待领取</StatusLabel>
                    )}
                  </TableCell>

                  <TableCell className={cn(CELL, 'px-3 text-right')}>
                    {row.streak_days ? (
                      <span className="tnum inline-flex items-center gap-1 text-xs">
                        {row.streak_days > 1 ? <Flame className="size-3 text-warning" /> : null}
                        {row.streak_days}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>

                  <TableCell className={cn(CELL, 'px-3 text-right')}>
                    {row.today_credit === undefined ? (
                      <span className="text-muted-foreground">—</span>
                    ) : (
                      <span className="tnum text-xs">
                        <span className="font-medium">{formatCredits(row.today_credit)}</span>
                        <span className="text-muted-foreground">
                          {' / '}
                          {formatCredits(row.daily_credit ?? null)}
                        </span>
                      </span>
                    )}
                  </TableCell>

                  <TableCell className={cn(CELL, 'px-3 text-right')}>
                    <span className="tnum text-xs text-muted-foreground">
                      {formatCredits(row.total_credits ?? null)}
                    </span>
                  </TableCell>

                  <TableCell className={cn(CELL, 'max-w-[12rem] truncate px-3 text-2xs text-muted-foreground')}>
                    {row.theme_name || row.activity_name || '—'}
                  </TableCell>

                  <TableCell className={cn(CELL, 'whitespace-nowrap px-3 text-2xs text-muted-foreground')}>
                    {row.end_time ?? '—'}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>

      {/* 操作条：把"当前会领什么"写在按钮旁边，不用去数勾了几个 */}
      <div className="flex flex-wrap items-center gap-3 border-t border-border-subtle px-3 py-2">
        <p className="text-2xs text-muted-foreground">
          {claimable.length === 0 ? (
            '今天没有可领取的账号'
          ) : effectiveSelection.length === 0 ? (
            <>
              勾选要领取的账号 —— 签到<b className="font-medium text-foreground">不会自动执行</b>
              ，需你显式确认
            </>
          ) : (
            <>
              已选 <span className="tnum font-medium text-foreground">{effectiveSelection.length}</span> 个
              ，预计可得{' '}
              <span className="tnum font-medium text-foreground">
                {formatCredits(totalSelectedCredit)}
              </span>{' '}
              积分
            </>
          )}
        </p>

        <div className="ml-auto flex items-center gap-1.5">
          {claimable.length > 0 && effectiveSelection.length < claimable.length ? (
            <Button
              size="xs"
              variant="ghost"
              onClick={() => setSelected(new Set(claimableIds))}
            >
              全选未领取（{claimable.length}）
            </Button>
          ) : null}
          <Button
            size="xs"
            disabled={effectiveSelection.length === 0 || claim.isPending}
            onClick={() => setConfirmOpen(true)}
          >
            <Gift />
            {claim.isPending ? '领取中…' : `领取选中（${effectiveSelection.length}）`}
          </Button>
        </div>
      </div>

      {/* 二次确认：把要发生的后果逐条列出来 */}
      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认领取签到？</AlertDialogTitle>
            <AlertDialogDescription>
              将对下列 {selectedRows.length} 个账号发起真实领取请求，每个账号的每日领取机会只能用一次。
            </AlertDialogDescription>
          </AlertDialogHeader>

          <ul className="scroll-thin max-h-48 space-y-1 overflow-y-auto text-xs">
            {selectedRows.map((row) => (
              <li key={row.account_id} className="flex items-center justify-between gap-3 rounded-sm bg-muted px-2 py-1">
                <span className="font-mono text-2xs">{row.uin}</span>
                <span className="tnum text-2xs text-muted-foreground">
                  +{formatCredits(row.today_credit ?? null)} 积分
                  {row.streak_days ? ` · 连续 ${row.streak_days} 天` : ''}
                </span>
              </li>
            ))}
          </ul>

          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                claim.mutate(effectiveSelection)
                setSelected(new Set())
              }}
            >
              确认领取
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
