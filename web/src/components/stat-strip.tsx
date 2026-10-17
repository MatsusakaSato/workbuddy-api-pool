/**
 * KPI 条。
 *
 * 这是对整个界面观感影响最大的一个组件，设计意图很明确：
 *
 * ❌ 反面做法：6–9 个等权重的彩色大卡片。人眼扫描依赖视觉差异，等权重等于没有优先序；
 *    彩色面积过大又会让"红色"同时表示强调和类别，状态语义随之崩塌。
 *
 * ✅ 这里：一行 inline 统计，发丝竖线分隔，无卡片、无阴影、无彩色底。
 *    正常状态**整条只有一个颜色**（前景色），只有真正需要注意的数值才着色 ——
 *    于是颜色一出现就必然意味着"看这里"。
 *
 * ★ 2026-10-16 改版补充：彩色版把「标签」这一层做成了品牌色小字。
 *   这是刻意的取舍 —— 数值保持深色（保证扫读清晰），颜色加在**标签**上：
 *   面积小、不抢数值、又让整条不再是纯灰。数值仍遵循上面的规矩：
 *   只有 tone 出现时才着色，且 tone 只表示状态，不表示类别。
 *
 * ⚠️ 曾经在这条顶部加过一条 2px 品牌渐变横线，**已去掉**。
 *   反馈是"蓝色的线条太显眼、有点突兀"。复盘原因：KPI 条在宽屏下是 6 列，
 *   6 个蓝标签本身已经足够表达"这里有颜色"；再加一条横贯全宽的饱和渐变线，
 *   就变成了整条上最重的一笔 —— 而它不承载任何信息，纯装饰。
 *   结论：颜色只保留在**有语义的标签文字**上，不要再加装饰性横线。
 *   （同理，顶栏底部那条渐变线也一并去掉了，见 index.css 的说明。）
 *
 * 尺寸上刻意压扁：标签 12px、数值 20px、上下 padding 12px。
 * 一屏能多放两行表格，这对运维面板比"看起来气派"重要得多。
 */

import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'
import type { AccountTone } from '@/lib/format'

const VALUE_TONE: Record<AccountTone, string> = {
  success: 'text-success',
  warning: 'text-warning',
  danger: 'text-danger',
  idle: 'text-foreground',
}

export interface StatItem {
  label: string
  value: ReactNode
  /** 可选说明，显示在标签旁（如口径注释） */
  hint?: string
  /** 只在需要引起注意时给 tone；正常留空即沿用前景色 */
  tone?: AccountTone
  /** 数值后的小单位/后缀，用弱化色 */
  suffix?: ReactNode
}

export function StatStrip({
  items,
  className,
}: {
  items: StatItem[]
  className?: string
}) {
  return (
    <dl
      className={cn(
        // 窄屏两列，宽屏一行平铺；用 grid 而不是 flex-wrap，
        // 保证每一列宽度一致，数值不会因为位数变化而互相推挤
        'grid grid-cols-2 gap-x-0 gap-y-3 rounded-lg bg-card px-0 py-0 shadow-border sm:grid-cols-3 lg:grid-cols-4 xl:flex xl:items-stretch xl:gap-y-0 xl:py-0',
        className,
      )}
    >
      {items.map((item, index) => (
        <div
          key={item.label}
          className={cn(
            'min-w-0 px-3 py-3 xl:flex-1',
            // 发丝竖线：只加在非首列，且不跨越换行后的第一个
            index > 0 && 'xl:border-l xl:border-border-subtle',
          )}
        >
          <dt className="flex items-baseline gap-1 text-2xs font-medium text-primary">
            <span className="truncate">{item.label}</span>
            {item.hint ? (
              <span className="shrink-0 text-2xs text-muted-foreground/70">{item.hint}</span>
            ) : null}
          </dt>
          <dd className="mt-0.5 flex items-baseline gap-1">
            <span
              className={cn(
                // tnum 是必须的：轮询刷新时数字宽度不变，整条不会左右抖动
                'tnum text-xl font-semibold tracking-tight',
                item.tone ? VALUE_TONE[item.tone] : 'text-foreground',
              )}
            >
              {item.value}
            </span>
            {item.suffix ? (
              <span className="tnum text-xs text-muted-foreground">{item.suffix}</span>
            ) : null}
          </dd>
        </div>
      ))}
    </dl>
  )
}
