/**
 * 就地反馈的复制按钮。
 *
 * 为什么不用全局 toast：复制是**局部动作**，用户视线就在按钮上。
 * 弹一个右下角的 toast 反而要人移动视线去确认，是负优化。
 * 所以按钮本体变成对勾，1200ms 后自己淡回来。
 *
 * 键盘可达：Button 本身就是 button 元素，Enter/Space 天然可用。
 */

import { useEffect, useRef, useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { copyText } from '@/lib/clipboard'
import { cn } from '@/lib/utils'

/** 统一的"复制成功后显示对勾"计时器 */
function useCopyFeedback() {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const timer = useRef<number | undefined>(undefined)

  useEffect(() => () => window.clearTimeout(timer.current), [])

  const trigger = async (value: string): Promise<void> => {
    const ok = await copyText(value)
    setState(ok ? 'copied' : 'failed')
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setState('idle'), 1200)
  }

  return { state, trigger }
}

type Size = 'icon-xs' | 'icon-sm' | 'xs' | 'sm'
type Variant = 'ghost' | 'outline' | 'secondary'

export function CopyButton({
  value,
  label,
  size = 'icon-xs',
  variant = 'ghost',
  className,
  /** 带文字时显示这个标签 */
  children,
}: {
  value: string
  /** tooltip 与无障碍名称，如 "复制 base_url" */
  label: string
  size?: Size
  variant?: Variant
  className?: string
  children?: React.ReactNode
}) {
  const { state, trigger } = useCopyFeedback()
  const tooltip =
    state === 'failed' ? '复制失败，请手动选中' : state === 'copied' ? '已复制' : label

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant={variant}
          size={size}
          onClick={() => void trigger(value)}
          aria-label={label}
          className={cn('shrink-0', className)}
        >
          {state === 'copied' ? (
            <Check className="text-success" />
          ) : (
            <Copy className={state === 'failed' ? 'text-danger' : undefined} />
          )}
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{tooltip}</TooltipContent>
    </Tooltip>
  )
}

/**
 * 可点击复制的行内文本。
 *
 * 用于表格里的 ID、模型名、地址这类"看一眼就要拿去别处用"的值。
 * `cursor: copy` 是浏览器原生的语义光标，比 pointer 更准确地表达"点了会复制"。
 */
export function ClickToCopy({
  value,
  label,
  className,
  children,
}: {
  value: string
  /** 无障碍名称，如 "复制账号 ID" */
  label: string
  className?: string
  children?: React.ReactNode
}) {
  const { state, trigger } = useCopyFeedback()

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={label}
          onClick={() => void trigger(value)}
          className={cn(
            'inline-flex max-w-full cursor-copy items-center gap-1 rounded-sm text-left outline-none',
            'hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring',
            state === 'copied' && 'text-success',
            state === 'failed' && 'text-danger',
            className,
          )}
        >
          <span className="truncate">{children ?? value}</span>
          {state === 'copied' ? (
            <Check className="size-3 shrink-0" aria-hidden />
          ) : null}
        </button>
      </TooltipTrigger>
      <TooltipContent>
        {state === 'failed' ? '复制失败，请手动选中' : state === 'copied' ? '已复制' : label}
      </TooltipContent>
    </Tooltip>
  )
}
