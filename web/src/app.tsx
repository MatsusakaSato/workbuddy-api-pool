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

import { lazy, Suspense, useEffect } from 'react'
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { HashRouter, Navigate, Outlet, Route, Routes, useLocation } from 'react-router'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Toaster } from '@/components/ui/sonner'
import { Skeleton } from '@/components/ui/skeleton'
import { AppTopbar } from '@/components/app-topbar'
import { ErrorBoundary } from '@/components/error-boundary'
import { KeyGate } from '@/components/key-gate'
import { ApiError } from '@/lib/api'
import { hasKey } from '@/lib/key'
import { useHealth, useStatus } from '@/hooks/use-gateway'

/**
 * 路由级代码分割。
 *
 * 2026-10-16 更新：原本拆分的动机是 recharts（一家约 400 kB，未压缩）。
 * 用量页的柱状图现已换成自绘的（见 components/viz.tsx），recharts 不再被任何页面引用。
 *
 * 同日第二次变更：**「用量」页并入「概览」**（见 routes/overview.tsx）。
 * 用量区块与概览同属首屏，拆 chunk 已经没有意义 —— 它反而会多一次往返。
 * 于是 UsagePage 的懒加载被删掉，用量相关组件直接进首屏 chunk。
 *
 * 拆分本身仍然保留：懒加载让「接入」页的 shiki 语法高亮
 * （含 python/shell/js 多套语法定义）不会压进首屏。
 */
const OverviewPage = lazy(() =>
  import('@/routes/overview').then((m) => ({ default: m.OverviewPage })),
)
const ModelsPage = lazy(() => import('@/routes/models').then((m) => ({ default: m.ModelsPage })))
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

/**
 * 布局：顶栏 + 内容区。
 *
 * 侧栏已移除 —— 导航只有 3 项，撑不起一整列；横排进顶栏后
 * 内容区每屏多出 15rem，「模型」这类网格页受益最明显。
 * ⌘K 命令面板随搜索功能一并下线。
 */
function Layout() {
  return (
    <div className="flex h-svh min-h-0 flex-col">
      <AppTopbar />
      <main className="min-h-0 flex-1 overflow-y-auto">
        <ErrorBoundary>
          <Suspense fallback={<RouteFallback />}>
            <Outlet />
          </Suspense>
        </ErrorBoundary>
      </main>
    </div>
  )
}

/**
 * 旧的 `#/usage` 深链 → 概览。
 *
 * 「用量」已并入概览（2026-10-16），但这个地址可能已经进了书签、也可能被文档引用，
 * 所以保留一条重定向而不是让它掉进"未知路径"。
 * **必须把 query 带过去**：`#/usage?w=d90` 里的窗口选择应当继续生效 ——
 * 丢掉它意味着用户的历史书签会静默换回默认窗口。
 */
function UsageRedirect() {
  const { search } = useLocation()
  return <Navigate to={{ pathname: '/overview', search }} replace />
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
                {/* 旧地址，见 UsageRedirect 的说明 */}
                <Route path="/usage" element={<UsageRedirect />} />
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
