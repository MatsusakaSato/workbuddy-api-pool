/**
 * 应用外壳与路由。
 *
 * 路由用 HashRouter 而不是 BrowserRouter：这样**服务端完全不需要 SPA 回退**
 * （不用把未知路径都吐 dashboard.html），gateway/server.py 一行都不用改。
 * 代价是 URL 里有个 #，对本地工具完全可以接受。
 *
 * 启动时会先跑 legacy-deeplink 的翻译（见 main.tsx），把旧的 ?tab=… 深链
 * 转换成 #/<name>，所以老书签不会失效。
 */

import { lazy, Suspense, useEffect, useState } from 'react'
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { HashRouter, Navigate, Outlet, Route, Routes } from 'react-router'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Toaster } from '@/components/ui/sonner'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import { Skeleton } from '@/components/ui/skeleton'
import { AppSidebar } from '@/components/app-sidebar'
import { AppTopbar } from '@/components/app-topbar'
import { CommandPalette } from '@/components/command-palette'
import { ErrorBoundary } from '@/components/error-boundary'
import { KeyGate } from '@/components/key-gate'
import { ApiError } from '@/lib/api'
import { hasKey } from '@/lib/key'
import { useHealth, useStatus } from '@/hooks/use-gateway'

/**
 * 路由级代码分割。
 *
 * 主要是为了 recharts：它一家约 400 kB（未压缩），而只有「用量」页用它。
 * 不拆的话，打开概览页也要先下载这 400 kB —— 对一个本地工具是没必要的浪费。
 * 拆完之后首屏只加载外壳 + 概览，图表在真正切到用量页时才拉。
 * （实测：拆分前单包 944 kB，拆分后首屏约 365 kB。）
 */
const OverviewPage = lazy(() =>
  import('@/routes/overview').then((m) => ({ default: m.OverviewPage })),
)
const ModelsPage = lazy(() => import('@/routes/models').then((m) => ({ default: m.ModelsPage })))
const UsagePage = lazy(() => import('@/routes/usage').then((m) => ({ default: m.UsagePage })))
const ConnectPage = lazy(() =>
  import('@/routes/connect').then((m) => ({ default: m.ConnectPage })),
)

/**
 * 懒加载占位。
 * 不做全屏 spinner —— 本地加载通常几十毫秒就完了，转圈反而像卡住。
 * 用与内容同高的骨架，看起来是"内容正在长出来"而不是"页面被清空"。
 */
function RouteFallback() {
  return (
    <div className="flex flex-col gap-4 p-4 lg:p-5">
      <Skeleton className="h-7 w-32" />
      <Skeleton className="h-[74px] rounded-lg" />
      <Skeleton className="h-64 rounded-lg" />
    </div>
  )
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 面板在本地跑，切回窗口时顺手刷新一下是符合预期的
      refetchOnWindowFocus: true,
      // 10 分钟没被观察就回收，避免长时间开着堆积缓存
      gcTime: 10 * 60_000,
      staleTime: 0,
    },
  },
})

/* ------------------------------------------------------------------ */
/* 密钥闸门：在渲染整个应用之前先决定"能不能用"                        */
/* ------------------------------------------------------------------ */

function KeyGuard({ children }: { children: React.ReactNode }) {
  const status = useStatus()
  const health = useHealth()

  // /healthz 不需要 key，所以"服务在不在"和"key 对不对"要分开判断：
  // 服务都没起来的话，提示填 key 是误导。
  const authError =
    (status.error instanceof ApiError && status.error.isAuthError) ||
    (health.error instanceof ApiError && health.error.isAuthError)

  if (!hasKey()) return <KeyGate reason="missing" />
  if (authError) {
    const message =
      status.error instanceof ApiError && status.error.isAuthError ? status.error.message : undefined
    return <KeyGate reason="rejected" serverMessage={message} />
  }
  return <>{children}</>
}

/* ------------------------------------------------------------------ */
/* 布局                                                                */
/* ------------------------------------------------------------------ */

function Layout() {
  const [commandOpen, setCommandOpen] = useState(false)

  // ⌘K / Ctrl+K 打开命令面板。与 sidebar 自己的 ⌘B 折叠快捷键并存。
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key.toLowerCase() !== 'k' || !(event.metaKey || event.ctrlKey)) return
      // 不要抢掉输入框里的正常快捷键（比如 textarea 里的粘贴）
      event.preventDefault()
      setCommandOpen((open) => !open)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [])

  return (
    <SidebarProvider
      // 16rem 对密集控制台偏宽，收到 15rem
      style={{ '--sidebar-width': '15rem' } as React.CSSProperties}
    >
      <AppSidebar />
      <SidebarInset className="min-w-0">
        <AppTopbar onOpenCommand={() => setCommandOpen(true)} />
        <div className="min-w-0 flex-1">
          <ErrorBoundary>
            <Suspense fallback={<RouteFallback />}>
              <Outlet />
            </Suspense>
          </ErrorBoundary>
        </div>
      </SidebarInset>
      <CommandPalette open={commandOpen} onOpenChange={setCommandOpen} />
    </SidebarProvider>
  )
}

/** key 变化后需要把缓存整个丢掉 —— 否则旧 key 拿到的数据会留在界面上 */
export function ResetOnKeyChange() {
  const qc = useQueryClient()
  useEffect(() => {
    const onStorage = (event: StorageEvent): void => {
      if (event.key === 'wbgw_key') void qc.invalidateQueries()
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [qc])
  return null
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={300}>
        <HashRouter>
          <ResetOnKeyChange />
          <KeyGuard>
            <Routes>
              <Route element={<Layout />}>
                <Route index element={<Navigate to="/overview" replace />} />
                <Route path="/overview" element={<OverviewPage />} />
                <Route path="/models" element={<ModelsPage />} />
                <Route path="/usage" element={<UsagePage />} />
                <Route path="/connect" element={<ConnectPage />} />
                {/* 未知路径回概览，不显示 404 —— 本地工具不需要这一层 */}
                <Route path="*" element={<Navigate to="/overview" replace />} />
              </Route>
            </Routes>
          </KeyGuard>
        </HashRouter>
        {/* 全局提示条。就地反馈优先（如复制按钮），这里只放跨页面的结果。 */}
        <Toaster position="bottom-right" closeButton />
      </TooltipProvider>
    </QueryClientProvider>
  )
}
