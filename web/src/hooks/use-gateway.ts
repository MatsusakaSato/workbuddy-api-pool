/**
 * 网关数据的查询层（TanStack Query）。
 *
 * 几个刻意的决定：
 *  · 轮询只在页面可见时进行（refetchIntervalInBackground: false）——
 *    本地工具没必要在后台标签页里一直打请求。
 *  · 一律用 keepPreviousData，否则每次轮询都会把表格清空再填回去，闪得厉害。
 *  · 401/403 不重试（key 错了重试一百次也没用），网络类错误重试一次。
 *  · 写操作成功后精准失效受影响的查询，而不是全量 invalidate。
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  keepPreviousData,
  type UseQueryResult,
} from '@tanstack/react-query'
import { toast } from 'sonner'
import { ApiError, api } from '@/lib/api'
import type {
  CheckinOverview,
  ClaimResponse,
  Health,
  LogResponse,
  ModelEntry,
  SelftestResponse,
  Settings,
  StatusSnapshot,
  UsageResponse,
} from '@/lib/types'

/* ------------------------------------------------------------------ */
/* 轮询节奏（毫秒）                                                    */
/* ------------------------------------------------------------------ */

/** 与旧面板一致：账号状态 5 秒一次，这是"实时感"的来源 */
export const POLL = {
  status: 5_000,
  log: 10_000,
  checkin: 60_000,
  usage: 60_000,
} as const

/** key 错了就别重试；网络抖动重试一次 */
function retry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && (error.isAuthError || error.status === 403)) return false
  return failureCount < 1
}

export const queryKeys = {
  health: ['health'] as const,
  status: ['status'] as const,
  checkin: ['checkin'] as const,
  settings: ['settings'] as const,
  models: ['models'] as const,
  log: (limit: number) => ['log', limit] as const,
  usage: (days: number) => ['usage', days] as const,
}

/* ------------------------------------------------------------------ */
/* 查询                                                                */
/* ------------------------------------------------------------------ */

/** 服务端是否活着。不需要 key，所以 key 没配时也能用来判断"服务在不在" */
export function useHealth(): UseQueryResult<Health, Error> {
  return useQuery<Health, Error>({
    queryKey: queryKeys.health,
    queryFn: api.health,
    refetchInterval: POLL.status,
    refetchIntervalInBackground: false,
    retry,
    staleTime: 0,
  })
}

export function useStatus(enabled = true): UseQueryResult<StatusSnapshot, Error> {
  return useQuery<StatusSnapshot, Error>({
    queryKey: queryKeys.status,
    queryFn: api.status,
    enabled,
    refetchInterval: POLL.status,
    refetchIntervalInBackground: false,
    placeholderData: keepPreviousData,
    retry,
  })
}

export function useCheckin(enabled = true): UseQueryResult<CheckinOverview, Error> {
  return useQuery<CheckinOverview, Error>({
    queryKey: queryKeys.checkin,
    queryFn: api.checkin,
    enabled,
    refetchInterval: POLL.checkin,
    refetchIntervalInBackground: false,
    placeholderData: keepPreviousData,
    retry,
    // 签到状态一天只变一次，没必要每次进页面都重新拉
    staleTime: 30_000,
  })
}

/** 设置几乎不变，缓存久一点 */
export function useSettings(enabled = true): UseQueryResult<Settings, Error> {
  return useQuery<Settings, Error>({
    queryKey: queryKeys.settings,
    queryFn: api.settings,
    enabled,
    staleTime: 5 * 60_000,
    retry,
  })
}

/** 模型目录要翻目录、可能打上游，缓存 5 分钟 */
export function useModels(enabled = true): UseQueryResult<{ data: ModelEntry[] }, Error> {
  return useQuery<{ data: ModelEntry[] }, Error>({
    queryKey: queryKeys.models,
    queryFn: api.models,
    enabled,
    staleTime: 5 * 60_000,
    placeholderData: keepPreviousData,
    retry,
  })
}

export function useLog(limit = 80, enabled = true): UseQueryResult<LogResponse, Error> {
  return useQuery<LogResponse, Error>({
    queryKey: queryKeys.log(limit),
    queryFn: () => api.log(limit),
    enabled,
    refetchInterval: POLL.log,
    refetchIntervalInBackground: false,
    placeholderData: keepPreviousData,
    retry,
  })
}

export function useUsage(days = 30, enabled = true): UseQueryResult<UsageResponse, Error> {
  return useQuery<UsageResponse, Error>({
    queryKey: queryKeys.usage(days),
    queryFn: () => api.usage(days),
    enabled,
    refetchInterval: POLL.usage,
    refetchIntervalInBackground: false,
    placeholderData: keepPreviousData,
    retry,
  })
}

/* ------------------------------------------------------------------ */
/* 写操作                                                              */
/* ------------------------------------------------------------------ */

/** 重新读取凭据 + 刷新积分。会打上游拉余额，慢一点是正常的。 */
export function useReload() {
  const qc = useQueryClient()
  return useMutation<StatusSnapshot, Error, void>({
    mutationFn: api.reload,
    onSuccess: (snapshot) => {
      // 直接把新快照塞进缓存，省掉一次往返
      qc.setQueryData(queryKeys.status, snapshot)
      void qc.invalidateQueries({ queryKey: queryKeys.checkin })
      void qc.invalidateQueries({ queryKey: queryKeys.settings })
      const available = snapshot.accounts.filter((a) => a.available).length
      toast.success(`已重载 ${snapshot.accounts.length} 个账号`, {
        description: `当前可用 ${available} 个`,
      })
    },
    onError: (error) => toast.error('重载失败', { description: error.message }),
  })
}

/** 端到端自检。**会消耗极少量积分**，且会被记进用量（服务端有意为之）。 */
export function useSelftest() {
  const qc = useQueryClient()
  return useMutation<SelftestResponse, Error, string>({
    mutationFn: api.selftest,
    onSuccess: (result) => {
      if (result.ok) {
        toast.success('自检通过', {
          description: `${result.account ?? ''} · ${result.reply ?? ''} · ${result.latency_ms ?? 0}ms`,
        })
      } else {
        toast.error(`自检失败${result.stage ? `（${result.stage}）` : ''}`, {
          description: result.error ?? '未知错误',
        })
      }
      // 自检真的花了积分，所以用量与日志都会变
      void qc.invalidateQueries({ queryKey: ['usage'] })
      void qc.invalidateQueries({ queryKey: ['log'] })
      void qc.invalidateQueries({ queryKey: queryKeys.status })
    },
    onError: (error) => toast.error('自检请求失败', { description: error.message }),
  })
}

/**
 * 立即签到（全自动签到的兜底按钮）。
 *
 * ★ 2026-10-16 变更：签到已改为**服务端全自动**（gateway/checkin.py 定时领取），
 * 前端不再有勾选 + 二次确认的表单。这个 mutation 只服务于「立即签到」按钮 ——
 * 它的语义是"别等下一轮，现在就跑一遍"。
 *
 * 服务端走的是与自动调度**同一条**路径（先查状态、只领今天没领的），
 * 所以结果里会出现三种情况，提示必须分开说，不能混成一句"N 个成功"：
 *  · claimed        —— 这次真的领到了
 *  · already(_local) —— 今天本来就领过（不是"又领了一份"）
 *  · 其它           —— 失败或活动未开始
 */
export function useClaimCheckin() {
  const qc = useQueryClient()
  return useMutation<ClaimResponse, Error, void>({
    mutationFn: api.claimCheckinAll,
    onSuccess: (data) => {
      const claimed = data.results.filter((r) => r.action === 'claimed')
      const already = data.results.filter(
        (r) => r.action === 'already' || r.action === 'already_local',
      )
      const failed = data.results.filter((r) => !r.ok)
      if (claimed.length > 0) {
        toast.success(`已签到 ${claimed.length} 个账号`, {
          description: already.length > 0 ? `另有 ${already.length} 个今天已经领过` : undefined,
        })
      } else if (failed.length > 0) {
        toast.warning('这次没有签到成功', {
          description: data.results.map((r) => r.reason).filter(Boolean).join('；') || undefined,
        })
      } else {
        toast.success('今天都已经领过了', {
          description: `共 ${already.length} 个账号`,
        })
      }
      void qc.invalidateQueries({ queryKey: queryKeys.checkin })
      void qc.invalidateQueries({ queryKey: queryKeys.status })
      void qc.invalidateQueries({ queryKey: ['usage'] })
    },
    onError: (error) => toast.error('签到失败', { description: error.message }),
  })
}
