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
          <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
          {description ? (
            <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
      </div>

      {children}
    </div>
  )
}
