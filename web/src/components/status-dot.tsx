/**
 * 状态展示原语。
 *
 * 设计要点（这是"看起来专业"和"看起来业余"的分水岭之一）：
 *  · 状态用 **6px 圆点 + 单色文字**，不用彩色胖 pill。
 *    一行里出现三五个彩色圆角标签就是典型的"Badge 汤"：彩色面积过大 → 强调失效，
 *    而且胖 pill 会挤压行高，让表格看起来松松垮垮。
 *  · 圆点只用一种颜色，不描边不留白，尺寸固定 6px —— 视觉重量恒定，
 *    换状态时行内不会有任何位移。
 *  · 需要一块浅色底（横幅、提示条）时才用 chip 形态，圆角 4px 而不是 9999px。
 */

import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import type { AccountTone } from '@/lib/format'

const DOT_CLASS: Record<AccountTone, string> = {
  success: 'bg-success-dot',
  warning: 'bg-warning-dot',
  danger: 'bg-danger-dot',
  idle: 'bg-idle-dot',
}

const TEXT_CLASS: Record<AccountTone, string> = {
  success: 'text-success',
  warning: 'text-warning',
  danger: 'text-danger',
  idle: 'text-muted-foreground',
}

const CHIP_CLASS: Record<AccountTone, string> = {
  success: 'bg-success-subtle border-success-border text-success',
  warning: 'bg-warning-subtle border-warning-border text-warning',
  danger: 'bg-danger-subtle border-danger-border text-danger',
  idle: 'bg-idle-subtle border-idle-border text-muted-foreground',
}

export function StatusDot({
  tone,
  pulse = false,
  className,
}: {
  tone: AccountTone
  /** 只在"有活动"时用（例如正在轮询/请求进行中），常态不要动 —— 会分散注意力 */
  pulse?: boolean
  className?: string
}) {
  return (
    <span
      aria-hidden
      className={cn(
        'inline-block size-1.5 shrink-0 rounded-full',
        DOT_CLASS[tone],
        pulse && 'animate-wbgw-pulse',
        className,
      )}
    />
  )
}

/** 圆点 + 文字。表格里的状态列用这个。 */
export function StatusLabel({
  tone,
  children,
  title,
  className,
}: {
  tone: AccountTone
  children: ReactNode
  title?: string
  className?: string
}) {
  return (
    <span
      className={cn('inline-flex items-center gap-1.5 text-xs font-medium', TEXT_CLASS[tone], className)}
      title={title}
    >
      <StatusDot tone={tone} />
      {children}
    </span>
  )
}

/** 浅色底片的 chip。只用在横幅、提示条这种需要"一块区域"的地方。 */
export function StatusChip({
  tone,
  children,
  className,
}: {
  tone: AccountTone
  children: ReactNode
  className?: string
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-0.5 text-xs font-medium',
        CHIP_CLASS[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}
