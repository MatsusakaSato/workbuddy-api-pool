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

/**
 * 只有异常语气才用状态色。常态用**品牌蓝**（`primary-subtle` / `primary`），
 * 不再留灰 —— 彩色版里，空状态是页面上少见的"大面积留白"，
 * 用一块品牌蓝图标底能让空页面也显得是"设计过的"。
 *
 * ⚠️ 这里和"颜色只表示状态"的纪律有一个明确的边界：
 * 蓝在这里表示**品牌/中性**，不表示"出事了"。真正表达异常的仍是 danger/warning，
 * 而且是同一族色系之外的红与橙，语义不会混。
 * （注意 primary 与 info 是同一个蓝：这在本项目里是有意的复用，
 *   info 语义只出现在 request-log 的「流式」标记上，两处不会同屏互相干扰。）
 */
const TONE_CLASS: Partial<Record<AccountTone, string>> = {
  danger: 'bg-danger-subtle text-danger',
  warning: 'bg-warning-subtle text-warning',
}

/** 常态（idle / 未指定）的图标底：品牌蓝 */
const IDLE_CLASS = 'bg-primary-subtle text-primary'

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
  const iconClass = TONE_CLASS[tone] ?? IDLE_CLASS

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
