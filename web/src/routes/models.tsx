/**
 * 模型目录页。
 *
 * 51 个模型如果用"一个大平表"呈现，找东西基本靠翻。所以三件事一起做：
 *   分面（全部/免费/国内/国际，带计数）→ 排序（家族/倍率/上下文）→ 搜索
 * 默认按**家族分组**：同一家族的 flash/pro/mini 版本挨在一起才方便比价，
 * 按字母序会把它们打散到表格各处。
 *
 * 视图状态全部放在 URL hash 里（`#/models?q=…&facet=…&sort=…`）：
 *  · 旧面板的 `?tab=models&facet=free` 深链能翻译过来（见 legacy-deeplink）；
 *  · 命令面板里选中一个模型 → `#/models?q=<id>`，落地即筛选；
 *  · 想把这批筛选结果发给别人/存书签，直接复制地址栏即可。
 */

import { Fragment, useMemo } from 'react'
import { useSearchParams } from 'react-router'
import { Search, X } from 'lucide-react'
import { Page } from '@/components/page'
import { Panel } from '@/components/panel'
import { EmptyState } from '@/components/empty-state'
import { ClickToCopy } from '@/components/copy-button'
import { StatusLabel } from '@/components/status-dot'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useModels } from '@/hooks/use-gateway'
import {
  formatCreditMultiplier,
  formatInt,
  formatTokens,
  modelFamily,
  regionLabel,
} from '@/lib/format'
import { cn } from '@/lib/utils'
import type { ModelEntry } from '@/lib/types'

type FacetKey = 'all' | 'free' | 'cn' | 'global'
type SortKey = 'family' | 'cost' | 'ctx' | 'name'

const FACETS: { key: FacetKey; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'free', label: '免费' },
  { key: 'cn', label: '国内' },
  { key: 'global', label: '国际' },
]

const SORTS: { key: SortKey; label: string }[] = [
  { key: 'family', label: '按家族' },
  { key: 'cost', label: '按倍率' },
  { key: 'ctx', label: '按上下文' },
  { key: 'name', label: '按名称' },
]

const HEAD = 'h-8 px-3 text-2xs font-medium text-muted-foreground whitespace-nowrap'
const CELL = 'h-8 px-3 py-0 align-middle'

function matches(model: ModelEntry, facet: FacetKey): boolean {
  if (facet === 'all') return true
  if (facet === 'free') return model.cost === 0
  return model.region === facet
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
 * 倍率单元格。三种情况必须分开，混了就是误导：
 *   null  → "—" + 说明（上游目录没提供，**不代表免费**）
 *   0     → "免费"（用状态色点出来，这是真的不要积分）
 *   > 0   → 等宽数字，"x0.05"，纵向可比
 */
function CostCell({ model }: { model: ModelEntry }) {
  if (model.cost === null) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="tnum cursor-default text-xs text-muted-foreground">—</span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">
          上游目录没有提供该模型的倍率（图像类模型常见），
          <b className="font-medium">不代表免费</b>。
        </TooltipContent>
      </Tooltip>
    )
  }
  if (model.cost === 0) return <StatusLabel tone="success">免费</StatusLabel>
  return <span className="tnum text-xs">{formatCreditMultiplier(model.credits)}</span>
}

function ModelRow({ model }: { model: ModelEntry }) {
  return (
    <TableRow className="group">
      <TableCell className={cn(CELL, 'max-w-[16rem]')}>
        <ClickToCopy
          value={model.id}
          label={`复制模型 ID ${model.id}`}
          className="font-mono text-2xs text-foreground"
        />
      </TableCell>
      <TableCell className={cn(CELL, 'max-w-[14rem]')}>
        <span className="block truncate text-xs">{model.name}</span>
      </TableCell>
      <TableCell className={cn(CELL, 'whitespace-nowrap')}>
        <span className="text-2xs text-muted-foreground">{regionLabel(model.region)}</span>
      </TableCell>
      <TableCell className={cn(CELL, 'text-right')}>
        <span className="tnum text-xs" title={`${formatInt(model.context_window)} tokens`}>
          {formatTokens(model.context_window)}
        </span>
      </TableCell>
      <TableCell className={cn(CELL, 'text-right')}>
        <span className="tnum text-xs text-muted-foreground">{formatTokens(model.max_tokens)}</span>
      </TableCell>
      <TableCell className={cn(CELL, 'text-right')}>
        <CostCell model={model} />
      </TableCell>
    </TableRow>
  )
}

export function ModelsPage() {
  const [params, setParams] = useSearchParams()
  const models = useModels()

  const q = params.get('q') ?? ''
  const facet = (params.get('facet') as FacetKey | null) ?? 'all'
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
      cn: all.filter((m) => m.region === 'cn').length,
      global: all.filter((m) => m.region === 'global').length,
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

  /** 只有按家族排序时才插入分组标题行 */
  const groups = useMemo(() => {
    if (sort !== 'family') return null
    const out: { family: string; items: ModelEntry[] }[] = []
    for (const model of rows) {
      const family = modelFamily(model.id)
      const last = out[out.length - 1]
      if (last && last.family === family) last.items.push(model)
      else out.push({ family, items: [model] })
    }
    return out
  }, [rows, sort])

  const isLoading = models.isLoading && !models.data

  return (
    <Page
      title="模型"
      description={
        all.length > 0
          ? `网关共暴露 ${all.length} 个模型，其中 ${counts.free} 个免费。倍率即积分系数，按上游目录实时同步。`
          : '正在读取上游模型目录…'
      }
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
      {/* 筛选条与表格同属一个 Panel：它们是"同一个列表的不同视图"，不该拆成两块 */}
      <Panel flush>
        <div className="flex flex-wrap items-center gap-2 border-b border-border-subtle px-3 py-2">
          <div className="relative min-w-[12rem] flex-1 sm:max-w-xs">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={q}
              onChange={(e) => patch({ q: e.target.value })}
              placeholder="搜索模型名或 ID…"
              aria-label="搜索模型"
              className="h-8 pl-8 text-xs"
              spellCheck={false}
            />
            {q ? (
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label="清除搜索"
                onClick={() => patch({ q: null })}
                className="absolute top-1/2 right-1 -translate-y-1/2"
              >
                <X />
              </Button>
            ) : null}
          </div>

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
                      ? 'bg-card text-foreground shadow-border'
                      : 'text-muted-foreground hover:text-foreground',
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
          <div className="space-y-1.5 p-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-8 rounded-sm" />
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
          <div className="scroll-thin max-h-[calc(100svh-14rem)] overflow-auto">
            <Table className="text-xs">
              <TableHeader className="sticky top-0 z-10 bg-card">
                <TableRow className="hover:bg-transparent">
                  <TableHead className={HEAD}>模型 ID</TableHead>
                  <TableHead className={HEAD}>名称</TableHead>
                  <TableHead className={HEAD}>区域</TableHead>
                  <TableHead className={cn(HEAD, 'text-right')}>上下文</TableHead>
                  <TableHead className={cn(HEAD, 'text-right')}>最大输出</TableHead>
                  <TableHead className={cn(HEAD, 'text-right')}>倍率</TableHead>
                </TableRow>
              </TableHeader>

              <TableBody>
                {/* 分组模式插入家族标题行；平铺模式直接按排序输出 */}
                {groups
                  ? groups.map((group) => (
                      <Fragment key={group.family}>
                        <TableRow className="hover:bg-transparent">
                          <TableCell
                            colSpan={6}
                            className="h-6 bg-muted/60 px-3 py-0 text-2xs font-medium text-muted-foreground"
                          >
                            <span className="font-mono">{group.family}</span>
                            <span className="tnum ml-2 opacity-70">{group.items.length}</span>
                          </TableCell>
                        </TableRow>
                        {group.items.map((model) => (
                          <ModelRow key={model.id} model={model} />
                        ))}
                      </Fragment>
                    ))
                  : rows.map((model) => <ModelRow key={model.id} model={model} />)}
              </TableBody>
            </Table>
          </div>
        )}
      </Panel>
    </Page>
  )
}
