/**
 * 命令面板（⌘K / Ctrl+K）。
 *
 * 组内约定：
 *  · 「跳转」在搜索框为空时展示，避免和模型结果抢位置
 *  · 模型结果最多 8 条 —— 51 个模型全铺出来反而找不到；
 *    要全量检索就去模型页（那里有分面、排序和完整列表）
 *  · 动作项带明确的后果提示（比如自检会消耗积分），不藏
 */

import { useMemo } from 'react'
import { useNavigate } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import {
  Boxes,
  ChartColumn,
  Copy,
  LayoutDashboard,
  Plug,
  RefreshCw,
  Stethoscope,
} from 'lucide-react'
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
  CommandShortcut,
} from '@/components/ui/command'
import { useModels, useReload, useSelftest, useSettings } from '@/hooks/use-gateway'
import { copyText } from '@/lib/clipboard'
import { toast } from 'sonner'

const MAX_MODEL_RESULTS = 8

export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const models = useModels()
  const settings = useSettings()
  const reload = useReload()
  const selftest = useSelftest()

  const modelRows = useMemo(
    () => (models.data?.data ?? []).slice(0, MAX_MODEL_RESULTS),
    [models.data],
  )

  /** 所有动作都先关面板再执行，否则弹层会和动作结果叠在一起 */
  const run = (fn: () => void) => {
    onOpenChange(false)
    // 让 Dialog 的关闭动画走完再执行，避免焦点被抢回去
    window.setTimeout(fn, 0)
  }

  const go = (path: string) => run(() => void navigate(path))

  const baseUrl = settings.data?.base_url

  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      title="命令面板"
      description="跳转页面、检索模型、执行操作"
    >
      <CommandInput placeholder="跳转、搜模型、执行操作…" />
      <CommandList>
        <CommandEmpty>没有匹配项</CommandEmpty>

        <CommandGroup heading="跳转">
          <CommandItem value="概览 overview 账号 签到 健康" onSelect={() => go('/overview')}>
            <LayoutDashboard />
            概览
            <CommandShortcut>账号与签到</CommandShortcut>
          </CommandItem>
          <CommandItem value="模型 models 目录 倍率" onSelect={() => go('/models')}>
            <Boxes />
            模型
            <CommandShortcut>{settings.data?.models_total ?? ''} 个</CommandShortcut>
          </CommandItem>
          <CommandItem value="用量 usage 统计 积分 延迟" onSelect={() => go('/usage')}>
            <ChartColumn />
            用量
            <CommandShortcut>按日聚合</CommandShortcut>
          </CommandItem>
          <CommandItem value="接入 connect 配置 编辑器 sdk" onSelect={() => go('/connect')}>
            <Plug />
            接入
            <CommandShortcut>连接配置</CommandShortcut>
          </CommandItem>
        </CommandGroup>

        <CommandSeparator />

        <CommandGroup heading="操作">
          <CommandItem
            value="重载 reload 刷新凭据 余额"
            disabled={reload.isPending}
            onSelect={() => run(() => reload.mutate())}
          >
            <RefreshCw />
            {reload.isPending ? '重载中…' : '重载凭据并刷新余额'}
          </CommandItem>
          <CommandItem
            value="自检 selftest 端到端 消耗积分"
            disabled={selftest.isPending}
            onSelect={() => run(() => selftest.mutate('deepseek-v4-flash'))}
          >
            <Stethoscope />
            {selftest.isPending ? '自检中…' : '端到端自检'}
            {/* 明确说出代价，不要让人点了才发现花了积分 */}
            <CommandShortcut className="text-warning">消耗少量积分</CommandShortcut>
          </CommandItem>
          <CommandItem
            value="复制 base_url 地址 接入"
            disabled={!baseUrl}
            onSelect={() =>
              run(async () => {
                if (!baseUrl) return
                const ok = await copyText(baseUrl)
                if (ok) toast.success('已复制 base_url', { description: baseUrl })
                else toast.error('复制失败', { description: '请手动选中复制' })
              })
            }
          >
            <Copy />
            复制 base_url
            <CommandShortcut className="font-mono">{baseUrl ?? '—'}</CommandShortcut>
          </CommandItem>
          <CommandItem
            value="刷新全部数据 refresh"
            onSelect={() => run(() => void qc.invalidateQueries())}
          >
            <RefreshCw />
            刷新全部数据
          </CommandItem>
        </CommandGroup>

        {modelRows.length > 0 ? (
          <>
            <CommandSeparator />
            <CommandGroup heading="模型">
              {modelRows.map((model) => (
                <CommandItem
                  key={model.id}
                  value={`${model.id} ${model.name}`}
                  onSelect={() => go(`/models?q=${encodeURIComponent(model.id)}`)}
                >
                  <Boxes />
                  <span className="truncate font-mono text-xs">{model.id}</span>
                  <CommandShortcut className="truncate">{model.name}</CommandShortcut>
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        ) : null}
      </CommandList>
    </CommandDialog>
  )
}
