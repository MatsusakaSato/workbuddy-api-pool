/**
 * 顶栏（现在也是唯一的导航处）。
 *
 * 原本左侧有条 15rem 的侧栏，但导航只有 4 项，撑不起一整列 ——
 * 空荡荡的反而显得页面没做完。所以砍掉侧栏，导航横排进顶栏：
 * 4 项 + 图标 + 角标，一行放得下，内容区每屏多出 15rem。
 *
 * ★ 2026-10-16：导航 4 项 → **3 项**。「用量」并入「概览」后不再单独成页，
 *   用量信息在概览页里作为区块出现（见 routes/overview.tsx）。
 *
 * 只放全局动作（刷新、连接状态）与当前页提示 ——
 * 页面自己的操作按钮放在各页的页头，不要往上堆，否则顶栏会变成杂物抽屉。
 * 搜索已按需求移除（⌘K 命令面板与「搜索…」按钮一并下线）。
 */

import { useIsFetching, useQueryClient } from '@tanstack/react-query'
import { NavLink, useLocation } from 'react-router'
import { Boxes, LayoutDashboard, Plug, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { StatusDot } from '@/components/status-dot'
import { useHealth, useStatus } from '@/hooks/use-gateway'
import { formatInt, formatRelative } from '@/lib/format'
import { cn } from '@/lib/utils'
import { ROUTES, type RouteName } from '@/lib/legacy-deeplink'

const HINTS: Record<RouteName, string> = {
  overview: '账号健康、自动签到、用量与最近请求',
  models: '可用模型目录与倍率',
  connect: '把网关接到你的编辑器或 SDK',
}

/** 横排导航项。图标各不重复；角标只挂有信息量的东西，零值不显示。 */
const NAV: { to: RouteName; label: string; icon: typeof Boxes }[] = [
  { to: 'overview', label: '概览', icon: LayoutDashboard },
  { to: 'models', label: '模型', icon: Boxes },
  { to: 'connect', label: '接入', icon: Plug },
]

export function AppTopbar() {
  const location = useLocation()
  const qc = useQueryClient()
  const health = useHealth()
  const status = useStatus()

  // 有任何请求在飞就转一下 —— 这是"系统在干活"的最轻量反馈
  const fetching = useIsFetching() > 0

  const active = (ROUTES as readonly string[]).find((r) =>
    location.pathname.startsWith(`/${r}`),
  ) as RouteName | undefined

  // 角标数据：有问题账号数 / 模型总数（沿用原侧栏的取数口径）。
  // 「今日失败数」原来挂在「用量」项上；用量并入概览后它重复了 ——
  // 概览里已经有失败数的 KPI、健康横幅与最近请求日志，不再需要一个只显示数字的角标。
  const problemAccounts = (status.data?.accounts ?? []).filter(
    (a) => !a.available || a.exhausted || (!a.token_valid && !a.refresh_valid),
  ).length
  // 目录大小（来自 /admin/status 的 catalog）
  const modelCount = status.data?.catalog ?? 0
  const badgeFor = (to: RouteName): { n?: number; danger?: boolean } => {
    if (to === 'overview') return { n: problemAccounts || undefined, danger: true }
    if (to === 'models') return { n: modelCount || undefined }
    return {}
  }

  const refresh = (): void => {
    void qc.invalidateQueries()
  }

  return (
    /* ⚠️ 顶栏底部原本有一条 2px 品牌渐变横线，**已去掉**。
       反馈是"蓝色的线条太显眼、有点突兀"。复盘：顶栏本身已经是横贯全屏的横条，
       再在它下面压一条全宽饱和渐变线，等于把"横线"这个形状重复了一遍，
       而且它不承载任何信息。去掉之后品牌感由 Logo 方块与导航选中态承担，
       反而更聚焦。
       （header 仍保留 border-b 作为与内容区的结构分界，见下。） */
    <header className="sticky top-0 z-20 flex h-12 shrink-0 items-center gap-3 border-b border-border bg-background/85 px-3 backdrop-blur-sm">
      {/* 品牌：紧凑一段，点击回概览 */}
      <NavLink to="/overview" className="flex shrink-0 items-center gap-2">
        <span
          aria-hidden
          className="flex size-6 items-center justify-center rounded-md bg-primary text-primary-foreground shadow-primary"
        >
          <Plug className="size-3.5" />
        </span>
        <span className="hidden text-sm font-semibold tracking-tight sm:inline">
          WorkBuddy 网关
        </span>
      </NavLink>

      <Separator orientation="vertical" className="h-4" />

      {/* 横排主导航：替代原侧栏 */}
      <nav aria-label="主导航" className="flex items-center gap-0.5">
        {NAV.map((item) => {
          const badge = badgeFor(item.to)
          const isActive = active === item.to
          return (
            <NavLink
              key={item.to}
              to={`/${item.to}`}
              aria-current={isActive ? 'page' : undefined}
              className={cn(
                'inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-xs transition-colors',
                'outline-none focus-visible:ring-2 focus-visible:ring-ring',
                /* 选中态用品牌蓝实心底 + 白字（4.87:1）。
                   不用浅蓝底：浅蓝底在顶栏的浅色背景上几乎看不出来，
                   而彩色主题最需要的就是"当前位置"一眼可辨。 */
                isActive
                  ? 'bg-primary font-medium text-primary-foreground shadow-primary'
                  : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
              )}
            >
              <item.icon className="size-3.5" />
              {item.label}
              {badge.n ? (
                <span
                  className={cn(
                    'tnum text-2xs leading-none',
                    /* 选中项的底是品牌蓝，danger 红放上去只有 1.9:1 不可读，
                       所以选中时角标改白（在蓝底上 4.87:1）。 */
                    isActive
                      ? 'text-primary-foreground'
                      : badge.danger
                        ? 'text-danger'
                        : 'text-muted-foreground',
                  )}
                >
                  {formatInt(badge.n)}
                </span>
              ) : null}
            </NavLink>
          )
        })}
      </nav>

      {active ? (
        <span className="hidden truncate text-2xs text-muted-foreground lg:inline">
          {HINTS[active]}
        </span>
      ) : null}

      <div className="ml-auto flex items-center gap-1.5">
        {/* 连接状态：文字 + 圆点。不只是一个孤零零的圆点，避免用户猜颜色含义 */}
        <NavLink
          to="/overview"
          className="mr-1 hidden items-center gap-1.5 rounded-md px-1.5 py-1 text-2xs transition-colors hover:bg-accent md:inline-flex"
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
      </div>
    </header>
  )
}
