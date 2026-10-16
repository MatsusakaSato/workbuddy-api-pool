/**
 * 左侧主导航。
 *
 * 只有四项，图标各不重复，选中项靠 sidebar-accent 底色区分 —— 不做彩色高亮，
 * 因为颜色在这个界面里是**状态语义**专用的，导航占用颜色会削弱状态的表达力。
 *
 * 角标只挂有信息量的东西：
 *  · 概览 —— 有问题的账号数（过期/失效/耗尽），为 0 时不显示
 *  · 模型 —— 可用模型总数
 *  · 用量 —— 今天的失败请求数，为 0 时不显示
 * 零值不显示是关键：常驻的 "0" 是噪音，会让人慢慢无视这个位置。
 */

import { NavLink } from 'react-router'
import {
  Boxes,
  ChartColumn,
  LayoutDashboard,
  Plug,
  type LucideIcon,
} from 'lucide-react'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from '@/components/ui/sidebar'
import { StatusDot } from '@/components/status-dot'
import { useHealth, useLog, useModels, useSettings, useStatus } from '@/hooks/use-gateway'
import { formatInt, formatRelative } from '@/lib/format'
import { fromUnixSeconds } from '@/lib/format'

interface NavEntry {
  to: string
  label: string
  icon: LucideIcon
  /** 角标数字；0 或 undefined 时不渲染 */
  badge?: number
  /** 角标是否按"告警"着色 */
  badgeTone?: 'neutral' | 'danger'
}

export function AppSidebar() {
  const health = useHealth()
  const status = useStatus()
  const settings = useSettings()
  const models = useModels()
  const log = useLog(1)

  // 有问题的账号：不可用、或凭据失效、或额度耗尽
  const problemAccounts = (status.data?.accounts ?? []).filter(
    (a) => !a.available || a.exhausted || (!a.token_valid && !a.refresh_valid),
  ).length

  const todayErrors = log.data?.stats.total_errors ?? 0
  const modelCount = settings.data?.models_total ?? models.data?.data.length

  const nav: NavEntry[] = [
    {
      to: '/overview',
      label: '概览',
      icon: LayoutDashboard,
      badge: problemAccounts || undefined,
      badgeTone: 'danger',
    },
    { to: '/models', label: '模型', icon: Boxes, badge: modelCount },
    {
      to: '/usage',
      label: '用量',
      icon: ChartColumn,
      badge: todayErrors || undefined,
      badgeTone: 'danger',
    },
    { to: '/connect', label: '接入', icon: Plug },
  ]

  const online = health.isSuccess
  const lastRefresh = fromUnixSeconds(status.data?.last_reload)

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild>
              <NavLink to="/overview" className="gap-2.5">
                <span
                  aria-hidden
                  className="flex aspect-square size-8 shrink-0 items-center justify-center rounded-md bg-info-solid text-info-foreground"
                >
                  <Plug className="size-4" />
                </span>
                <span className="grid min-w-0 flex-1 text-left leading-tight">
                  <span className="truncate text-sm font-semibold tracking-tight">
                    WorkBuddy 网关
                  </span>
                  <span className="tnum truncate text-2xs text-muted-foreground">
                    v{settings.data?.version ?? '—'}
                  </span>
                </span>
              </NavLink>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {nav.map((item) => (
                <SidebarMenuItem key={item.to}>
                  <SidebarMenuButton asChild tooltip={item.label}>
                    <NavLink to={item.to}>
                      {({ isActive }) => (
                        <>
                          <item.icon />
                          <span>{item.label}</span>
                          {isActive ? (
                            <span
                              aria-hidden
                              className="ml-auto h-4 w-0.5 rounded-full bg-foreground/70 group-data-[collapsible=icon]:hidden"
                            />
                          ) : null}
                        </>
                      )}
                    </NavLink>
                  </SidebarMenuButton>
                  {item.badge ? (
                    <SidebarMenuBadge
                      className={
                        item.badgeTone === 'danger'
                          ? 'tnum text-danger'
                          : 'tnum text-muted-foreground'
                      }
                    >
                      {formatInt(item.badge)}
                    </SidebarMenuBadge>
                  ) : null}
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter>
        {/* 连接状态常驻页脚：任何时候都能一眼知道"服务还活着吗" */}
        <div className="flex items-center gap-2 px-2 py-1.5 group-data-[collapsible=icon]:hidden">
          <StatusDot tone={online ? 'success' : 'danger'} pulse={online} />
          <div className="min-w-0 flex-1 leading-tight">
            <div className="truncate text-2xs font-medium">
              {online ? '服务正常' : health.isError ? '无法连接' : '连接中…'}
            </div>
            <div className="tnum truncate text-2xs text-muted-foreground">
              {lastRefresh ? `重载于 ${formatRelative(lastRefresh)}` : '尚未重载'}
            </div>
          </div>
        </div>
      </SidebarFooter>

      {/* 折叠把手：鼠标悬停在边缘时出现 */}
      <SidebarRail />
    </Sidebar>
  )
}
