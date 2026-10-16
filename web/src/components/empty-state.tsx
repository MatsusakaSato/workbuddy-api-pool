/**
 * 空状态。
 *
 * 一条硬规矩：**空状态必须给出下一步动作**。
 * 只写"暂无数据"等于把用户丢在原地 —— 他不知道是该等、该点哪里、还是自己配错了。
 *
 * 结构用的是组件库的 `Empty` 原语（Empty / EmptyHeader / EmptyMedia / EmptyTitle /
 * EmptyDescription / EmptyContent），**不自造布局**：
 * 间距、字号、`text-balance`、图标容器尺寸都由库统一维护，
 * 这里只加这一层应用语义（tone / icon / action），
 * 这样以后升级组件库时排版会跟着一起更新。
 *
 * 两种语气要分开：
 *  · tone="idle" —— 本来就没有数据（比如还没产生请求），给引导
 *  · tone="danger"/"warning" —— 加载失败，给重试
 */

import type { ReactNode } from 'react'
import { AlertTriangle, Inbox, type LucideIcon } from 'lucide-react'
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from '@/components/ui/empty'
import { cn } from '@/lib/utils'
import type { AccountTone } from '@/lib/format'

const TONE_ICON: Partial<Record<AccountTone, LucideIcon>> = {
  danger: AlertTriangle,
  warning: AlertTriangle,
}

/** 只有异常语气才着色；常态留灰，让颜色保持"有事"的信号价值 */
const TONE_CLASS: Partial<Record<AccountTone, string>> = {
  danger: 'bg-danger-subtle text-danger',
  warning: 'bg-warning-subtle text-warning',
}

export function EmptyState({
  title,
  description,
  action,
  tone = 'idle',
  icon,
  className,
}: {
  title: string
  description?: ReactNode
  /** 下一步动作。强烈建议提供 —— 没有动作的空状态基本没有价值。 */
  action?: ReactNode
  tone?: AccountTone
  icon?: LucideIcon
  className?: string
}) {
  const Icon = icon ?? TONE_ICON[tone] ?? Inbox
  const iconClass = TONE_CLASS[tone] ?? 'bg-muted text-muted-foreground'

  return (
    <Empty className={cn('py-10', className)}>
      <EmptyHeader>
        <EmptyMedia variant="icon" className={cn('size-9 rounded-lg', iconClass)}>
          <Icon />
        </EmptyMedia>
        <EmptyTitle className="text-sm">{title}</EmptyTitle>
        {description ? (
          <EmptyDescription className="text-xs">{description}</EmptyDescription>
        ) : null}
      </EmptyHeader>
      {action ? <EmptyContent className="flex-row justify-center">{action}</EmptyContent> : null}
    </Empty>
  )
}
