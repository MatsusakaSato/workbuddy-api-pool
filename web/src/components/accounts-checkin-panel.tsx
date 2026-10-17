/**
 * 账号面板（★ 2026-10-16：去表格 + 全自动签到）。
 *
 * 这一版是两个决定的结果，都来自 docs/可视化方案调研.md：
 *
 * ① **不再用表格，只保留 4 个字段**：账号 · 用户名 · 余额 · 是否签到。
 *    旧表 7 列里有 2 列是恒定值（「冷却」全是 0ms、「状态」全是"可用"）——
 *    恒定值列占着宽度却不承载信息（调研 §1 问题 ①）。
 *    现在每行是一个 grid 行：身份两列 + 一条量级条 + 一个状态结论。
 *    例外情况（账号不可用/额度耗尽）才在账号前多出一个小小的红点 ——
 *    **只在异常时出现**，所以它出现就必然意味着"看这里"，不会退化成又一根恒定列。
 *
 * ② **余额用横向条**（调研 §2.3）。上游只返回剩余积分、没有可用的分母，
 *    所以条长按**池内最大余额**归一：最富的那个满格，其余按比例。
 *    相对关系一眼可见，而且"快见底"是看得见的形状，不是等归零才知道。
 *    数字仍然保留在条的右侧：形状负责"快不快见底"，数字负责"到底多少"。
 *
 * ④ **余额分成「基础套餐」与「平台奖励」两桶显示**（2026-10-17）。
 *    上游把每个账号包单独一行返回，用 `CapacityType` 区分：4 = 月度订阅（基础套餐），
 *    其余 = 一次性赠送包（平台奖励）。两者**用途不同**：
 *      · 基础套餐**月底清零** —— 不用白不用，轮换会优先把它花掉；
 *      · 平台奖励过期晚得多，用不完的零头可以留到下个月。
 *    所以「合计 1,854」这个数字单独看是不够的：1,854 全是奖励、基础为 0，
 *    与「基础 500 + 奖励 1,354」是两种完全不同的处境。金额右侧给出明细，
 *    条本身也按两桶分段着色（深的在先，对应"先用谁的钱"）。
 *
 * ③ **签到改为服务端全自动**（gateway/checkin.py）。所以旧的勾选框、批量全选、
 *    二次确认弹窗**全部移除** —— 那是"表单"，而这里现在只回答"领了没有"。
 *    页脚保留一个「立即签到」按钮：不是主流程，是"别等下一轮，现在就跑"的兜底。
 *    自动签到的节奏与结果写在页脚，用户不必去猜它到底会不会自己领。
 *
 * 数据来源仍然是**两个独立接口**（status 与 checkin），账号集合可能不一致
 * （例如凭据可读但签到查询失败）。以 status 的账号为基准，用 account_id 关联；
 * 查不到就显示 "—"，**不假装"待签到"**。
 */

import { useMemo } from 'react'
import { Gift } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { StatusDot, StatusLabel } from '@/components/status-dot'
import { ClickToCopy } from '@/components/copy-button'
import { EmptyState } from '@/components/empty-state'
import { MagnitudeBar } from '@/components/viz'
import { useClaimCheckin } from '@/hooks/use-gateway'
import {
  accountState,
  formatBalance,
  formatClock,
  formatCredits,
  formatInt,
  formatRelative,
  formatUptime,
  fromUnixSeconds,
} from '@/lib/format'
import { cn } from '@/lib/utils'
import type {
  Account,
  CheckinAccount,
  CheckinAutoAccount,
  CheckinAutoStatus,
  UsageAccount,
} from '@/lib/types'

/**
 * 行的列宽。
 *
 * 为什么 4 列还要显式给比例：这是**扫读**需要的纵向对齐。
 * 账号列最宽（uin 是 12 位等宽数字），余额列次之（条 + 数字），
 * 用户名与签到状态只要够放最短的文本就行。
 */
const GRID =
  'grid grid-cols-[minmax(11rem,1.4fr)_minmax(7rem,0.9fr)_minmax(14rem,1fr)_minmax(7rem,0.7fr)] items-center gap-x-4 px-3'

/**
 * 余额格右侧的强制留白。
 *
 * ⚠️ 这里踩过的坑必须写下来：旧表格里「余额」右对齐、「今日签到」左对齐，
 * 两个格子的内容在边界相遇，实测最小间距只剩 **36px** —— 数字像是贴在了状态上。
 *
 * 所以数字列右对齐（与全站"数字右对齐"的规则一致，5 行数字也能成一条竖线），
 * 但**在右边强制留出 2.5rem**：40px 留白 + 16px 栅格间距 ⟹ 数字到状态至少 56px。
 * 这个值不是拍脑袋的：验收脚本断言"相邻字段可见内容间距 ≥ 40px"，它守着这条线。
 */
const BALANCE_GUTTER = 'pr-10'

/** 用户名格：昵称可能为空，显示 "—" 而不是回落成 uin（那会让这一列时名时数） */
function NicknameCell({ nickname }: { nickname: string }) {
  if (!nickname) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="cursor-default text-xs text-muted-foreground">—</span>
        </TooltipTrigger>
        <TooltipContent>凭据里没有用户名（点右上「重载凭据」可重试）</TooltipContent>
      </Tooltip>
    )
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="block cursor-default truncate text-xs">{nickname}</span>
      </TooltipTrigger>
      <TooltipContent>{nickname}</TooltipContent>
    </Tooltip>
  )
}

/**
 * 账号格的"异常红点"。
 *
 * 只在账号**不可用**或**额度耗尽**时出现 —— 常态下这里什么都没有。
 * 依据是调研 §1 问题 ①：一个恒定显示"可用"的列是纯占位；
 * 反过来，"只在异常时出现"的小点出现即等于"看这里"。
 */
function AccountAlert({ account }: { account: Account }) {
  if (account.available && !account.exhausted) return null
  const state = accountState(account)
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="shrink-0">
          <StatusDot tone={state.tone} />
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        <div>{state.label}</div>
        {state.detail ? <div className="mt-1 opacity-80">{state.detail}</div> : null}
        {account.last_error && state.detail !== account.last_error ? (
          <div className="mt-1 font-mono text-2xs opacity-80">{account.last_error}</div>
        ) : null}
      </TooltipContent>
    </Tooltip>
  )
}

/** 自动签到在某一行上的结论（tooltip 用） */
function autoLine(rec: CheckinAutoAccount | undefined): string | null {
  if (!rec) return null
  switch (rec.action) {
    case 'claimed':
      return `自动领取 +${formatCredits(rec.credited ?? null)} 积分`
    case 'claimed_manual':
      return `手动领取 +${formatCredits(rec.credited ?? null)} 积分`
    case 'already':
      return '上游确认今天已经领过'
    case 'already_local':
      return '本地记录：今天已经领过'
    case 'inactive':
      return '签到活动未开始或已结束'
    case 'unavailable':
      return '凭据不可用，没能查询'
    case 'unknown':
      return '上游没返回今日签到状态，没有盲领'
    case 'error':
      return `出错：${rec.error || '未知错误'}`
    default:
      return rec.action
  }
}

/** 是否签到格 */
function CheckinCell({
  row,
  auto,
}: {
  row: CheckinAccount | undefined
  auto: CheckinAutoStatus | undefined
}) {
  const rec = auto?.accounts?.[row?.account_id ?? '']

  if (!row) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="cursor-default text-xs text-muted-foreground">—</span>
        </TooltipTrigger>
        <TooltipContent>没有读到这个账号的签到信息（凭据可读但签到查询未成功）</TooltipContent>
      </Tooltip>
    )
  }

  if (row.error) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="cursor-default">
            <StatusLabel tone="danger">查询失败</StatusLabel>
          </span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">{row.error}</TooltipContent>
      </Tooltip>
    )
  }

  const checked = row.today_checked_in === true
  const detail = autoLine(rec)

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="cursor-default">
          <StatusLabel tone={checked ? 'success' : 'warning'}>
            {checked ? '已签到' : row.today_checked_in === false ? '待签到' : '状态未知'}
          </StatusLabel>
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        <div className="tnum">
          今日签到可得 {formatCredits(row.today_credit ?? null)} 积分
          {row.daily_credit ? ` · 每日 ${formatCredits(row.daily_credit)}` : ''}
        </div>
        {row.streak_days ? <div className="tnum mt-1">已连续 {row.streak_days} 天</div> : null}
        {detail ? <div className="mt-1 opacity-90">{detail}</div> : null}
        {!checked && auto?.enabled ? (
          <div className="mt-1 opacity-90">
            自动签到会在下一轮处理
            {auto.next_run_in_s !== null ? `（约 ${formatUptime(auto.next_run_in_s)} 后）` : ''}
          </div>
        ) : null}
        {!auto?.enabled ? (
          <div className="mt-1 opacity-90">自动签到已关闭，需要手动点「立即签到」</div>
        ) : null}
      </TooltipContent>
    </Tooltip>
  )
}

/** 页脚的自动签到摘要 —— 回答"它到底会不会自己领、上次领了没、下次什么时候" */
function AutoSummary({ auto, total }: { auto: CheckinAutoStatus | undefined; total: number }) {
  if (!auto) {
    return <span className="text-2xs text-muted-foreground">自动签到状态读取中…</span>
  }
  if (!auto.enabled) {
    return (
      <span className="text-2xs text-warning">
        自动签到已关闭（GATEWAY_AUTO_CHECKIN=0）—— 只能手动点「立即签到」
      </span>
    )
  }

  const last = fromUnixSeconds(auto.last_run_at)
  const failed = auto.last_summary?.failed ?? 0
  return (
    <span className="text-2xs text-muted-foreground">
      自动签到 · 每 {Math.round(auto.interval_s / 60)} 分钟一轮 · 今天已领{' '}
      <span className="tnum font-medium text-foreground">
        {auto.claimed_today}/{total}
      </span>
      {last ? (
        <>
          {' '}
          · 上次 <span className="tnum">{formatClock(last)}</span>（{formatRelative(last)}）
        </>
      ) : (
        ' · 尚未执行'
      )}
      {auto.next_run_in_s !== null ? (
        <>
          {' '}
          · 下一轮 <span className="tnum">{formatUptime(auto.next_run_in_s)}</span> 后
        </>
      ) : null}
      {failed > 0 ? <span className="text-danger"> · 上轮 {failed} 个失败</span> : null}
    </span>
  )
}

export function AccountsCheckinPanel({
  accounts,
  checkin,
  auto,
  usageByAccount,
  usageWindowLabel,
}: {
  accounts: Account[]
  checkin: CheckinAccount[]
  auto?: CheckinAutoStatus
  /** 窗口内按账号聚合，键是 uin；用于余额的 tooltip（明细见下方说明） */
  usageByAccount?: Map<string, UsageAccount>
  usageWindowLabel?: string
}) {
  const claim = useClaimCheckin()

  const checkinById = useMemo(() => {
    const map = new Map<string, CheckinAccount>()
    for (const row of checkin) map.set(row.account_id, row)
    return map
  }, [checkin])

  if (accounts.length === 0) {
    return (
      <EmptyState title="没有读到任何账号" description="确认桌面客户端已登录，然后点「重载凭据」。" />
    )
  }

  const available = accounts.filter((a) => a.available).length
  // 合计只加**已知**的账号：把"还没拉到"当成 0 会让合计凭空变少，
  // 而用户没法从界面上看出少了哪个号。unknown 单独计数，在页脚注明。
  const totalCredits = accounts.reduce((sum, a) => sum + (a.credits ?? 0), 0)
  const totalBase = accounts.reduce((sum, a) => sum + (a.base_credits ?? 0), 0)
  const totalReward = accounts.reduce((sum, a) => sum + (a.reward_credits ?? 0), 0)
  const unknownCredits = accounts.filter((a) => a.credits === null || a.credits === undefined).length
  const maxCredits = Math.max(0, ...accounts.map((a) => a.credits ?? 0))
  // 只数**上游明确说"今天还没领"**的账号。查询失败的、拿不到状态的都不计 ——
  // 那些属于"不知道"，把它们算进"还差几个"会让这个数字时大时小、无法核对。
  const pending = accounts.filter(
    (a) => checkinById.get(a.account_id)?.today_checked_in === false,
  ).length

  return (
    <>
      {/* 列头。只用 12px 次级灰，不做大写、不加图标 —— 图标在无信息时只是噪音 */}
      <div className={cn(GRID, 'h-7 border-b border-border-subtle text-2xs text-muted-foreground')}>
        <span>账号</span>
        <span>用户名</span>
        <Tooltip>
          <TooltipTrigger asChild>
            {/* 与数字列用同样的宽度和留白，这样列头正好压在数字列的右边缘上 */}
            <span className={cn('w-[16rem] cursor-default justify-self-end text-right', BALANCE_GUTTER)}>
              余额
            </span>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs">
            <div>条长按池内最大余额归一；条空了就是额度见底</div>
            <div className="mt-1 opacity-80">
              条内分段：深色 = 基础套餐（月底清零，轮换优先消耗），浅色 = 平台奖励
            </div>
          </TooltipContent>
        </Tooltip>
        <span>是否签到</span>
      </div>

      <ul aria-label="账号列表">
        {accounts.map((account) => {
          const row = checkinById.get(account.account_id)
          const usage = usageByAccount?.get(account.uin)
          const exhausted = (account.credits ?? 0) <= 0 && account.credits !== null
          const windowLabel = usageWindowLabel ?? '当前窗口'
          return (
            <li
              key={account.account_id}
              className={cn(
                GRID,
                // h-11（44px）= 余额格「条+合计」（16px）+「桶明细」（14px + 6px 间距）
                // 再留 8px。原来的 h-9（36px）装不下两行 —— 实测余额格高 42px，
                // 会把行撑开、压掉分隔线。
                'h-11 border-b border-border-subtle last:border-b-0 hover:bg-accent/40',
              )}
            >
              <div className="flex min-w-0 items-center gap-2">
                <AccountAlert account={account} />
                <ClickToCopy
                  value={account.uin}
                  label={`复制账号 ${account.uin}`}
                  className="tnum min-w-0 font-mono text-xs font-medium"
                />
              </div>

              <NicknameCell nickname={account.nickname} />

              {/* 余额：条 + 数字 + 两桶明细，整块右对齐。数字靠右成一条竖线（右对齐是硬规则），
                  条从数字往左生长 —— 这样"谁快见底"是往左读出来的，
                  而数字列本身保持整齐。右侧留白见 BALANCE_GUTTER。 */}
              <BalanceCell
                account={account}
                maxCredits={maxCredits}
                exhausted={exhausted}
                sublabel={
                  usage
                    ? `${windowLabel}：${formatInt(usage.requests)} 次请求 · ${formatCredits(usage.credits)} 积分`
                    : `${windowLabel}：没有请求`
                }
              />

              <CheckinCell row={row} auto={auto} />
            </li>
          )
        })}
      </ul>

      {/* 操作条：把"自动签到现在什么状态"和"还剩几个没领"写在按钮旁边 */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-border-subtle px-3 py-2">
        <AutoSummary auto={auto} total={accounts.length} />
        <span className="text-2xs text-muted-foreground">
          可用{' '}
          <span className="tnum font-medium text-foreground">
            {available}/{accounts.length}
          </span>{' '}
          · 合计余额{' '}
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="tnum cursor-default font-medium text-foreground">
                {formatBalance(totalCredits)}
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              <div className="tnum">
                基础套餐 {formatBalance(totalBase)} · 平台奖励 {formatBalance(totalReward)}
              </div>
              {unknownCredits > 0 ? (
                <div className="mt-1 text-warning">
                  另有 {unknownCredits} 个账号还没拉到余额，未计入合计
                </div>
              ) : null}
            </TooltipContent>
          </Tooltip>
          {auto?.enabled ? (
            <>
              {' '}
              · 今天还差 <span className="tnum font-medium text-foreground">{pending}</span> 个
            </>
          ) : null}
        </span>

        <Button
          size="xs"
          variant="outline"
          className="ml-auto"
          disabled={claim.isPending}
          onClick={() => claim.mutate()}
        >
          <Gift />
          {claim.isPending ? '签到中…' : '立即签到'}
        </Button>
      </div>
    </>
  )
}

/**
 * 余额格的配色。
 *
 * 深色 = 基础套餐（**会清零的钱**，轮换优先花它），浅色 = 平台奖励。
 * 顺序与"先用谁"一致：条的左端就是策略最先消耗的那一桶。
 * 用蓝而非红/绿：这里没有好坏之分，只有"在哪一桶里"。
 */
const BASE_SEGMENT = 'bg-chart-4'
const REWARD_SEGMENT = 'bg-chart-gradient-2'

/**
 * 余额格：合计数字 + 「基础 / 奖励」拆分明细 + 分段条（2026-10-17）。
 *
 * ## 为什么不能只显示一个合计
 *
 * 上游余额由两个桶组成，它们的**过期规则完全不同**：
 *   · 基础套餐（月度订阅，`CapacityType 4`）—— **月底清零**；
 *   · 平台奖励（签到/拉新赠送包，`CapacityType 1`）—— 各自有过期时间，普遍晚得多。
 *
 * 于是「合计 1,854」这个数字单独看是没有用的：全是奖励、基础为 0 的号，
 * 与「基础 500 + 奖励 1,354」的号，处境完全不同（前者这个月已经吃不到基础额度，
 * 后者还有 500 会被优先消耗）。明细必须和合计同时出现在同一行，
 * 而不是藏进 tooltip —— 藏起来的那个数字，正是用户此刻想知道的那个。
 *
 * ## 未知 ≠ 0
 *
 * `credits` 为 null 表示**还没拉到**（不是耗尽）。这时显示 "—" 且不画条，
 * 绝不能画成一根空条（那看起来就是"没额度了"）。
 */
function BalanceCell({
  account,
  maxCredits,
  exhausted,
  sublabel,
}: {
  account: Account
  maxCredits: number
  exhausted: boolean
  sublabel: string
}) {
  const base = account.base_credits ?? null
  const reward = account.reward_credits ?? null
  const hasSplit = base !== null || reward !== null
  const refreshAt = fromUnixSeconds(account.base_refresh_at ?? null)
  const expiringReward = account.reward_expiring_soon ?? 0
  const rewardExpiringAt = fromUnixSeconds(account.reward_expiring_soon_at ?? null)

  return (
    <div className={cn('w-[16rem] justify-self-end', BALANCE_GUTTER)}>
      <MagnitudeBar
        value={account.credits}
        max={maxCredits}
        tone={exhausted ? 'danger' : 'primary'}
        format={formatBalance}
        sublabel={
          account.credits_per_hour !== null && account.credits_per_hour !== undefined
            ? `${sublabel} · 本次运行消耗 ≈${formatCredits(account.credits_per_hour)}/时`
            : sublabel
        }
        segments={
          hasSplit
            ? [
                {
                  value: base ?? 0,
                  className: BASE_SEGMENT,
                  label: `基础套餐 ${formatBalance(base)}（月底清零${refreshAt ? `，${formatClock(refreshAt)} 刷新` : ''}）`,
                },
                {
                  value: reward ?? 0,
                  className: REWARD_SEGMENT,
                  label: `平台奖励 ${formatBalance(reward)}`,
                },
              ]
            : undefined
        }
      />
      <BalanceSplit
        base={base}
        reward={reward}
        exhausted={exhausted}
        refreshAt={refreshAt}
        expiringReward={expiringReward}
        rewardExpiringAt={rewardExpiringAt}
      />
    </div>
  )
}

/**
 * 合计下方的一行拆分：`基础 500 · 奖励 1,354`。
 *
 * 刻意**不给"合计"再写一遍**：条右侧的数字就是合计，重复只会让这一行变成噪音。
 * 两个桶都未知时整行不渲染（不显示 "— · —" 这种没有任何信息的占位）。
 */
function BalanceSplit({
  base,
  reward,
  exhausted,
  refreshAt,
  expiringReward,
  rewardExpiringAt,
}: {
  base: number | null
  reward: number | null
  exhausted: boolean
  refreshAt: Date | null
  expiringReward: number
  rewardExpiringAt: Date | null
}) {
  if (base === null && reward === null) return null

  // 基础为 0 且未耗尽：这个月的基础额度已经用完了。这不异常，但值得一眼看到 ——
  // 它意味着"接下来花的是本可以留到下个月的奖励积分"。
  const baseSpentOut = base !== null && base <= 0 && !exhausted

  return (
    <div className="mt-1 flex items-center justify-end gap-1.5 text-3xs leading-none">
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="flex cursor-default items-center gap-1">
            <span aria-hidden className={cn('size-2 rounded-[2px]', BASE_SEGMENT)} />
            <span className="text-muted-foreground">基础</span>
            <span className={cn('tnum', baseSpentOut ? 'text-warning' : 'text-foreground/80')}>
              {baseSpentOut ? '已用完' : formatBalance(base)}
            </span>
          </span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">
          <div>基础套餐积分 —— 月度订阅额度，**月底清零**</div>
          <div className="mt-1 opacity-80">
            {refreshAt ? `本周期 ${formatClock(refreshAt)} 结束，届时恢复到套餐上限` : '刷新时刻未知'}
          </div>
          <div className="mt-1 opacity-80">
            轮换会**优先消耗它**：同样的 1 分，它比奖励积分更早作废。
          </div>
        </TooltipContent>
      </Tooltip>

      <Tooltip>
        <TooltipTrigger asChild>
          <span className="flex cursor-default items-center gap-1">
            <span aria-hidden className={cn('size-2 rounded-[2px]', REWARD_SEGMENT)} />
            <span className="text-muted-foreground">奖励</span>
            <span
              className={cn('tnum', expiringReward > 0 ? 'text-warning' : 'text-foreground/80')}
            >
              {formatBalance(reward)}
            </span>
          </span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs">
          <div>平台奖励积分 —— 签到 / 拉新等一次性赠送包</div>
          <div className="mt-1 opacity-80">
            {rewardExpiringAt
              ? `最早的一批在 ${formatClock(rewardExpiringAt)} 到期`
              : '没有正在过期的奖励包'}
          </div>
          {expiringReward > 0 ? (
            <div className="mt-1 text-warning">
              其中 {formatBalance(expiringReward)} 将在 24 小时内过期
            </div>
          ) : null}
        </TooltipContent>
      </Tooltip>

    </div>
  )
}
