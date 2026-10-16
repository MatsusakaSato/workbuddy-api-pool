/**
 * 顶栏。
 *
 * 高度 48px，底部一条发丝分隔线，滚动时固定在顶部。
 * 只放全局动作（刷新、切主题、开命令面板）与当前页标题 ——
 * 页面自己的操作按钮放在各页的页头，不要往上堆，否则顶栏会变成杂物抽屉。
 */

import { useIsFetching, useQueryClient } from '@tanstack/react-query'
import { NavLink, useLocation } from 'react-router'
import { Command, Moon, RefreshCw, Sun } from 'lucide-react'
import { SidebarTrigger } from '@/components/ui/sidebar'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { Kbd } from '@/components/ui/kbd'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { StatusDot } from '@/components/status-dot'
import { useHealth, useStatus } from '@/hooks/use-gateway'
import { useTheme } from '@/hooks/use-theme'
import { formatRelative } from '@/lib/format'
import { cn } from '@/lib/utils'
import { ROUTES, type RouteName } from '@/lib/legacy-deeplink'

const TITLES: Record<RouteName, { title: string; hint: string }> = {
  overview: { title: '概览', hint: '账号健康、签到与最近请求' },
  models: { title: '模型', hint: '可用模型目录与倍率' },
  usage: { title: '用量', hint: '请求、积分与延迟的按日聚合' },
  connect: { title: '接入', hint: '把网关接到你的编辑器或 SDK' },
}

/** 快捷键提示。macOS 显示 ⌘，其它平台显示 Ctrl。 */
function shortcutLabel(key: string): string {
  const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)
  return isMac ? `⌘${key}` : `Ctrl+${key}`
}

export function AppTopbar({ onOpenCommand }: { onOpenCommand: () => void }) {
  const location = useLocation()
  const theme = useTheme()
  const qc = useQueryClient()
  const health = useHealth()
  const status = useStatus()

  // 有任何请求在飞就转一下 —— 这是"系统在干活"的最轻量反馈
  const fetching = useIsFetching() > 0

  const active = (ROUTES as readonly string[]).find((r) =>
    location.pathname.startsWith(`/${r}`),
  ) as RouteName | undefined
  const meta = active ? TITLES[active] : { title: 'WorkBuddy 网关', hint: '' }

  const refresh = (): void => {
    void qc.invalidateQueries()
  }

  return (
    <header className="sticky top-0 z-20 flex h-12 shrink-0 items-center gap-3 border-b border-border bg-background/85 px-3 backdrop-blur-sm">
      <SidebarTrigger className="-ml-0.5" />

      <Separator orientation="vertical" className="h-4" />

      <div className="flex min-w-0 items-baseline gap-2">
        <h1 className="truncate text-sm font-semibold tracking-tight">{meta.title}</h1>
        {meta.hint ? (
          <span className="hidden truncate text-2xs text-muted-foreground sm:inline">
            {meta.hint}
          </span>
        ) : null}
      </div>

      <div className="ml-auto flex items-center gap-1.5">
        {/* 连接状态：文字 + 圆点。不只是一个孤零零的圆点，避免用户猜颜色含义 */}
        <NavLink
          to="/overview"
          className="mr-1 hidden items-center gap-1.5 rounded-md px-1.5 py-1 text-2xs transition-colors hover:bg-muted md:inline-flex"
        >
          <StatusDot
            tone={health.isSuccess ? 'success' : health.isError ? 'danger' : 'idle'}
            pulse={health.isSuccess && fetching}
          />
          <span className="tnum text-muted-foreground">
            {health.isSuccess
              ? `${status.data?.accounts.length ?? 0} 个账号`
              : health.isError
                ? '服务未连接'
                : '连接中…'}
          </span>
        </NavLink>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={refresh}
              aria-label="刷新全部数据"
              className={cn(fetching && '[&_svg]:animate-spin')}
            >
              <RefreshCw />
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            刷新全部数据
            {status.dataUpdatedAt ? ` · 上次 ${formatRelative(new Date(status.dataUpdatedAt))}` : ''}
          </TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={theme.toggle}
              aria-label={theme.isDark ? '切换到亮色' : '切换到暗色'}
            >
              {theme.isDark ? <Sun /> : <Moon />}
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            {theme.isDark ? '切换到亮色' : '切换到暗色'}
            {theme.choice === 'system' ? '（当前跟随系统）' : ''}
          </TooltipContent>
        </Tooltip>

        <Button
          variant="outline"
          size="sm"
          onClick={onOpenCommand}
          className="gap-2 pl-2 pr-1.5 text-muted-foreground"
        >
          <Command className="size-3.5" />
          <span className="hidden sm:inline">搜索…</span>
          <Kbd className="tnum hidden font-mono sm:inline-flex">{shortcutLabel('K')}</Kbd>
        </Button>
      </div>
    </header>
  )
}
