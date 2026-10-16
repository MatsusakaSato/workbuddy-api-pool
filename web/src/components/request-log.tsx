/**
 * 最近请求日志。
 *
 * 这是排障时真正会被盯着看的东西，所以：
 *  · 时间、延迟、tokens、积分全部 tnum + 右对齐 —— 纵向要能扫出异常值；
 *  · 失败行不是"整行变红"，而是**结果列**用状态色 + 错误信息进 tooltip。
 *    整行染色会让表格变成报警灯墙，反而看不出哪一行有问题；
 *  · 测试流量（带 X-Gateway-Test 头）单独打标，因为它不进真实用量统计 ——
 *    不打标的话，看到日志里有请求但用量里没有，会以为是丢数据。
 */

import { useMemo, useState } from 'react'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { Toggle } from '@/components/ui/toggle'
import { StatusLabel } from '@/components/status-dot'
import { EmptyState } from '@/components/empty-state'
import { ClickToCopy } from '@/components/copy-button'
import { formatCredits, formatDuration, formatInt } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { LogItem } from '@/lib/types'

const HEAD = 'h-8 px-3 text-2xs font-medium text-muted-foreground whitespace-nowrap'
const CELL = 'h-8 px-3 py-0 align-middle'

export function RequestLog({ items }: { items: LogItem[] }) {
  const [onlyFailed, setOnlyFailed] = useState(false)

  const rows = useMemo(
    () => (onlyFailed ? items.filter((item) => !item.ok) : items),
    [items, onlyFailed],
  )
  const failedCount = useMemo(() => items.filter((item) => !item.ok).length, [items])

  if (items.length === 0) {
    return (
      <EmptyState
        title="还没有请求"
        description="网关一收到 /v1/chat/completions 就会在这里出现一行。可以先在「接入」页拿配置，或点右上「自检」发一条测试请求。"
      />
    )
  }

  return (
    <>
      <div className="flex items-center gap-2 px-3 py-2">
        <p className="text-2xs text-muted-foreground">
          最近 <span className="tnum">{items.length}</span> 条
          {failedCount > 0 ? (
            <>
              ，其中 <span className="tnum font-medium text-danger">{failedCount}</span> 条失败
            </>
          ) : null}
        </p>
        <Toggle
          size="sm"
          variant="outline"
          pressed={onlyFailed}
          onPressedChange={setOnlyFailed}
          disabled={failedCount === 0}
          className="ml-auto h-6 px-2 text-2xs"
          aria-label="只看失败的请求"
        >
          只看失败
        </Toggle>
      </div>

      <div className="scroll-thin max-h-[26rem] overflow-auto">
        <Table className="text-xs">
          <TableHeader className="sticky top-0 z-10 bg-card">
            <TableRow className="hover:bg-transparent">
              <TableHead className={HEAD}>时间</TableHead>
              <TableHead className={HEAD}>模型</TableHead>
              <TableHead className={HEAD}>账号</TableHead>
              <TableHead className={HEAD}>上游</TableHead>
              <TableHead className={cn(HEAD, 'text-right')}>延迟</TableHead>
              <TableHead className={cn(HEAD, 'text-right')}>tokens</TableHead>
              <TableHead className={cn(HEAD, 'text-right')}>积分</TableHead>
              <TableHead className={HEAD}>结果</TableHead>
            </TableRow>
          </TableHeader>

          <TableBody>
            {rows.map((item) => (
              <TableRow key={item.seq} className="group">
                <TableCell className={cn(CELL, 'whitespace-nowrap')}>
                  <span className="tnum text-2xs text-muted-foreground">{item.time}</span>
                </TableCell>

                <TableCell className={cn(CELL, 'max-w-[14rem]')}>
                  <div className="flex items-center gap-1.5">
                    <ClickToCopy
                      value={item.model}
                      label={`复制模型名 ${item.model}`}
                      className="font-mono text-2xs text-muted-foreground hover:text-foreground"
                    />
                    {/* 流式与否决定首字延迟的预期，是排障时要看的第一批信息 */}
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span
                          className={cn(
                            'shrink-0 cursor-default rounded-sm px-1 text-2xs',
                            item.stream
                              ? 'bg-info-subtle text-info'
                              : 'bg-muted text-muted-foreground',
                          )}
                        >
                          {item.stream ? '流' : '普'}
                        </span>
                      </TooltipTrigger>
                      <TooltipContent>
                        {item.stream ? '流式（SSE，边生成边返回）' : '非流式（一次性返回完整响应）'}
                      </TooltipContent>
                    </Tooltip>
                    {item.test ? (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span className="shrink-0 cursor-default rounded-sm bg-muted px-1 text-2xs text-muted-foreground">
                            测试
                          </span>
                        </TooltipTrigger>
                        <TooltipContent>
                          回归测试流量（带 X-Gateway-Test 头），单独记账，不计入你的用量
                        </TooltipContent>
                      </Tooltip>
                    ) : null}
                  </div>
                </TableCell>

                <TableCell className={cn(CELL, 'max-w-[10rem]')}>
                  <span className="tnum block truncate font-mono text-2xs text-muted-foreground">
                    {item.account || '—'}
                  </span>
                </TableCell>

                <TableCell className={cn(CELL, 'max-w-[12rem]')}>
                  <span className="block truncate text-2xs text-muted-foreground">
                    {item.upstream}
                  </span>
                </TableCell>

                {/* 延迟：首字延迟与总耗时分两列会太挤，这里显示总耗时，首字放 tooltip */}
                <TableCell className={cn(CELL, 'text-right')}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="tnum cursor-default text-xs">{formatDuration(item.total_ms)}</span>
                    </TooltipTrigger>
                    <TooltipContent className="tnum">
                      首字 {formatDuration(item.latency_ms)} · 总耗时 {formatDuration(item.total_ms)}
                    </TooltipContent>
                  </Tooltip>
                </TableCell>

                <TableCell className={cn(CELL, 'text-right')}>
                  <span className="tnum text-2xs text-muted-foreground">
                    {item.tokens ? formatInt(item.tokens) : '—'}
                  </span>
                </TableCell>

                <TableCell className={cn(CELL, 'text-right')}>
                  <span className="tnum text-2xs text-muted-foreground">
                    {item.credit ? formatCredits(item.credit) : '—'}
                  </span>
                </TableCell>

                <TableCell className={cn(CELL, 'whitespace-nowrap')}>
                  {item.ok ? (
                    <StatusLabel tone="success">成功</StatusLabel>
                  ) : (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="cursor-default">
                          <StatusLabel tone="danger">失败</StatusLabel>
                        </span>
                      </TooltipTrigger>
                      <TooltipContent className="max-w-sm">{item.error || '未记录错误信息'}</TooltipContent>
                    </Tooltip>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>

        {rows.length === 0 ? (
          <p className="px-3 py-6 text-center text-2xs text-muted-foreground">
            这段时间内没有失败的请求 —— 一切都好
          </p>
        ) : null}
      </div>
    </>
  )
}
