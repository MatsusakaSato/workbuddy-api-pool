/**
 * 模型目录页 —— 密集卡片矩阵。
 *
 * 布局演进的教训：51 个模型按 modelFamily 分组会产生 41 个家族，
 * 其中 33 个家族只有 1 个模型 —— "家族标题 + 孤卡"竖着排，
 * 每行右边 3/4 全是空的，看起来比表格还宽松。
 *
 * 所以这版用**一张连续密集网格**（网关只剩国内版一个上游，没有分区可言）：
 *   · 卡片 auto-fill 自适应 3-5 列，横向永远铺满，不留大空白；
 *   · 家族信息降级为卡片上的小徽标，排序为"按家族"时同家族的卡自然相邻；
 *   · 每张卡两行：ID（可复制）+ 倍率徽标，下面 名称 / 家族 / 参数条。
 *
 * 视图状态全部放在 URL hash 里（`#/models?q=…&facet=…&sort=…`）：
 *  · 旧面板的 `?tab=models&facet=free` 深链能翻译过来（见 legacy-deeplink）；
 *  · 命令面板里选中一个模型 → `#/models?q=<id>`，落地即筛选；
 *  · 想把这批筛选结果发给别人/存书签，直接复制地址栏即可。
 */

import { useMemo } from 'react'
import { useSearchParams } from 'react-router'
import { Page } from '@/components/page'
import { Panel } from '@/components/panel'
import { EmptyState } from '@/components/empty-state'
import { ClickToCopy } from '@/components/copy-button'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { StatusLabel } from '@/components/status-dot'
import { useModels } from '@/hooks/use-gateway'
import {
  formatCreditMultiplier,
  formatInt,
  formatTokens,
  modelFamily,
} from '@/lib/format'
import { cn } from '@/lib/utils'
import type { ModelEntry } from '@/lib/types'

type FacetKey = 'all' | 'free'
type SortKey = 'family' | 'cost' | 'ctx' | 'name'

const FACETS: { key: FacetKey; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'free', label: '免费' },
]

const FACET_KEYS = FACETS.map((f) => f.key)

const SORTS: { key: SortKey; label: string }[] = [
  { key: 'family', label: '按家族' },
  { key: 'cost', label: '按倍率' },
  { key: 'ctx', label: '按上下文' },
  { key: 'name', label: '按名称' },
]

function matches(model: ModelEntry, facet: FacetKey): boolean {
  if (facet === 'all') return true
  return model.cost === 0
}

function compare(a: ModelEntry, b: ModelEntry, sort: SortKey): number {
  if (sort === 'cost') {
    // null = 上游没给倍率（51 个里有 19 个，多为图像模型）。
    // 排序时沉到最后 —— "未知"不等于"免费"，不能混在 0 那一堆里。
    const ca = a.cost ?? Number.POSITIVE_INFINITY
    const cb = b.cost ?? Number.POSITIVE_INFINITY
    return ca - cb || a.id.localeCompare(b.id)
  }
  if (sort === 'ctx') {
    const ta = a.context_window ?? -1
    const tb = b.context_window ?? -1
    return tb - ta || a.id.localeCompare(b.id)
  }
  if (sort === 'name') {
    return a.name.localeCompare(b.name) || a.id.localeCompare(b.id)
  }
  return (
    modelFamily(a.id).localeCompare(modelFamily(b.id)) ||
    (a.cost ?? Number.POSITIVE_INFINITY) - (b.cost ?? Number.POSITIVE_INFINITY) ||
    a.id.localeCompare(b.id)
  )
}

/**
 * 倍率徽标。三种情况必须分开，混了就是误导：
 *   null  → "—" + 说明（上游目录没提供，**不代表免费**）
 *   0     → "免费"（用状态色点出来，这是真的不要积分）
 *   > 0   → 等宽数字，"x0.05"，横向可比
 */
function CostBadge({ model }: { model: ModelEntry }) {
  if (model.cost === null) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="tnum cursor-default text-2xs text-muted-foreground/70">倍率 —</span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">
          上游目录没有提供该模型的倍率（图像类模型常见），
          <b className="font-medium">不代表免费</b>。
        </TooltipContent>
      </Tooltip>
    )
  }
  if (model.cost === 0) return <StatusLabel tone="success">免费</StatusLabel>
  return (
    <span className="rounded-sm bg-muted px-1.5 py-0.5 font-mono text-2xs text-muted-foreground">
      {formatCreditMultiplier(model.credits)}
    </span>
  )
}

function ModelCard({ model }: { model: ModelEntry }) {
  return (
    <div className="group flex min-w-0 flex-col gap-1 rounded-md bg-muted/30 p-2.5 shadow-border-subtle transition-colors hover:bg-muted/50">
      {/* 第一行：ID（可复制）+ 倍率徽标 */}
      <div className="flex items-center justify-between gap-2">
        <ClickToCopy
          value={model.id}
          label={`复制模型 ID ${model.id}`}
          className="min-w-0 truncate font-mono text-2xs text-foreground"
        />
        <CostBadge model={model} />
      </div>

      {/* 第二行：名称（和 ID 几乎一样时省掉） */}
      <div className="flex items-center gap-1.5 text-2xs text-muted-foreground">
        {model.name && model.name.toLowerCase() !== model.id.toLowerCase() ? (
          <span className="min-w-0 truncate text-foreground/90">{model.name}</span>
        ) : null}
      </div>

      {/* 第三行：参数条 —— 等宽数字，点和值分层 */}
      <div className="flex items-center gap-x-2.5 text-2xs text-muted-foreground">
        <span className="tnum" title={`上下文 ${formatInt(model.context_window)} tokens`}>
          上下文 <b className="font-medium text-foreground/80">{formatTokens(model.context_window)}</b>
        </span>
        <span className="tnum" title={`最大输出 ${formatInt(model.max_tokens)} tokens`}>
          输出 <b className="font-medium text-foreground/80">{formatTokens(model.max_tokens)}</b>
        </span>
      </div>
    </div>
  )
}

export function ModelsPage() {
  const [params, setParams] = useSearchParams()
  const models = useModels()

  const q = params.get('q') ?? ''
  // 旧书签里可能带着已废弃的 facet=cn / facet=global，一律退回「全部」
  const rawFacet = params.get('facet')
  const facet: FacetKey =
    rawFacet && (FACET_KEYS as readonly string[]).includes(rawFacet)
      ? (rawFacet as FacetKey)
      : 'all'
  const sort = (params.get('sort') as SortKey | null) ?? 'family'

  const all = models.data?.data ?? []

  /** 只改指定参数，其余保持 —— 用 replace 避免每敲一个字就多一条历史记录 */
  const patch = (next: Record<string, string | null>): void => {
    const merged = new URLSearchParams(params)
    for (const [key, value] of Object.entries(next)) {
      if (value === null || value === '') merged.delete(key)
      else merged.set(key, value)
    }
    setParams(merged, { replace: true })
  }

  const counts = useMemo(
    () => ({
      all: all.length,
      free: all.filter((m) => m.cost === 0).length,
    }),
    [all],
  )

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return all
      .filter((m) => matches(m, facet))
      .filter((m) =>
        needle ? m.id.toLowerCase().includes(needle) || m.name.toLowerCase().includes(needle) : true,
      )
      .sort((a, b) => compare(a, b, sort))
  }, [all, facet, q, sort])

  const isLoading = models.isLoading && !models.data

  return (
    <Page
      title="模型"
      actions={
        <Button
          size="sm"
          variant="outline"
          disabled={models.isFetching}
          onClick={() => void models.refetch()}
        >
          {models.isFetching ? '同步中…' : '同步目录'}
        </Button>
      }
    >
      {/* 筛选条与卡片矩阵同属一个 Panel：它们是"同一个列表的不同视图"，不该拆成两块 */}
      <Panel flush>
        <div className="flex flex-wrap items-center gap-2 border-b border-border-subtle px-3 py-2">
          {/* 分面：分段控件形态。选中态用底色 + 前景色，不引入新的语义色 */}
          <div
            role="group"
            aria-label="按类别筛选"
            className="flex items-center gap-0.5 rounded-md bg-muted p-0.5"
          >
            {FACETS.map((item) => {
              const active = facet === item.key
              return (
                <button
                  key={item.key}
                  type="button"
                  aria-pressed={active}
                  onClick={() => patch({ facet: item.key === 'all' ? null : item.key })}
                  className={cn(
                    'inline-flex items-center gap-1.5 rounded-sm px-2 py-1 text-2xs font-medium transition-colors',
                    'outline-none focus-visible:ring-2 focus-visible:ring-ring',
                    active
                      ? 'bg-primary text-primary-foreground shadow-primary'
                      : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
                  )}
                >
                  {item.label}
                  <span className="tnum text-2xs opacity-70">{counts[item.key]}</span>
                </button>
              )
            })}
          </div>

          <div className="ml-auto flex items-center gap-2">
            <span className="tnum text-2xs text-muted-foreground">
              {rows.length} / {all.length}
            </span>
            <Select value={sort} onValueChange={(value) => patch({ sort: value })}>
              <SelectTrigger size="sm" className="h-7 w-[7.5rem] text-2xs" aria-label="排序方式">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SORTS.map((item) => (
                  <SelectItem key={item.key} value={item.key} className="text-xs">
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {isLoading ? (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(13.5rem,1fr))] gap-2 p-3">
            {Array.from({ length: 12 }).map((_, i) => (
              <Skeleton key={i} className="h-16 rounded-md" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            title="没有匹配的模型"
            description={
              q ? `没有模型名或 ID 包含「${q}」。` : '当前筛选条件下没有模型，换个分面试试。'
            }
            action={
              <Button
                size="xs"
                variant="outline"
                onClick={() => setParams(new URLSearchParams(), { replace: true })}
              >
                清除全部筛选
              </Button>
            }
          />
        ) : (
          <div className="scroll-thin max-h-[calc(100svh-13rem)] overflow-auto p-3">
            {/* 一张连续密集网格，横向永远铺满 */}
            <div className="grid grid-cols-[repeat(auto-fill,minmax(13.5rem,1fr))] gap-2">
              {rows.map((model) => (
                <ModelCard key={model.id} model={model} />
              ))}
            </div>
          </div>
        )}
      </Panel>
    </Page>
  )
}
