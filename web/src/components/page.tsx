/**
 * 页面外壳：统一的标题区 + 纵向节奏。
 *
 * 页面之间的间距、标题字号、描述位置全部收敛到这里，
 * 避免每一页各写一套 padding 然后慢慢长出 12/14/18px 混合的间距。
 */

import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

export function Page({
  title,
  description,
  actions,
  children,
  className,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-col gap-4 p-4 lg:p-5', className)}>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          {/* 标题左侧的品牌色竖条：很小的面积，但让每个页头都有明确的"这是本页"锚点。
              用 flex 而不是 border-left，避免竖条被行高拉伸到描述文字的高度。 */}
          <div className="flex items-center gap-2">
            <span
              aria-hidden
              className="h-4 w-1 shrink-0 rounded-full bg-brand-gradient"
            />
            <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
          </div>
          {description ? (
            <p className="mt-0.5 pl-3 text-xs text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
      </div>

      {children}
    </div>
  )
}
