/**
 * 区块容器（Panel）。
 *
 * 用 Geist 的「阴影描边」`0 0 0 1px` 而不是 border：
 *  · 不占布局空间 —— 有 border 时元素实际是 1px 更大，会出现 1px 的尺寸抖动；
 *  · 能贴合任意圆角，不会在圆角处露出直角边。
 *
 * 面板内**不再嵌套面板**。需要分组时用 SectionDivider 或直接用小标题 + 间距，
 * 层层套卡片是"界面看起来乱"的头号原因。
 */

import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export function Panel({
  title,
  description,
  actions,
  children,
  className,
  /** 内容区是否去掉内边距（表格自己控制间距时用） */
  flush = false,
}: {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  flush?: boolean
}) {
  const hasHeader = title || description || actions
  return (
    <section className={cn('rounded-lg bg-card shadow-border', className)}>
      {hasHeader ? (
        <header className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-3">
          <div className="min-w-0 flex-1">
            {title ? (
              <h2 className="truncate text-sm font-semibold tracking-tight">{title}</h2>
            ) : null}
            {description ? (
              <p className="mt-0.5 text-2xs text-muted-foreground">{description}</p>
            ) : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
        </header>
      ) : null}

      {/* 头部与内容之间用发丝线，仅在两者都存在时画 */}
      {hasHeader ? <div className="h-px bg-border-subtle" /> : null}

      <div className={flush ? '' : 'p-4'}>{children}</div>
    </section>
  )
}

/** 面板内的分区标题。比"再套一层卡片"轻得多，且不打断纵向节奏。 */
export function SectionLabel({
  children,
  trailing,
  className,
}: {
  children: ReactNode
  trailing?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex items-center justify-between gap-3', className)}>
      <h3 className="text-2xs font-medium text-muted-foreground">{children}</h3>
      {trailing}
    </div>
  )
}
