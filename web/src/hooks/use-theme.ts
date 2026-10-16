/**
 * 主题：light / dark / system。
 *
 * 刻意不用 next-themes：
 * dashboard.html 里那段预绘制脚本必须在首帧之前跑（否则刷新会闪白），
 * 而它读的就是 localStorage 的 wbgw_theme、挂的就是 .dark 类。
 * 自己实现一份可以保证「预绘制脚本」与「运行时 hook」用的是同一套约定，
 * 不会出现两套主题状态互相打架。
 *
 * 沿用旧面板的 localStorage 键名 wbgw_theme，所以老用户的偏好不会丢。
 * 旧版只支持 light/dark，这里扩到三态，读到的旧值依然有效。
 */

import { useCallback, useEffect, useState } from 'react'

export type ThemeChoice = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'wbgw_theme'
const DARK_QUERY = '(prefers-color-scheme: dark)'

function readStored(): ThemeChoice {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw === 'light' || raw === 'dark' || raw === 'system') return raw
  } catch {
    /* 隐私模式下 localStorage 可能抛错 */
  }
  return 'system'
}

function systemPrefersDark(): boolean {
  return typeof window !== 'undefined' && window.matchMedia(DARK_QUERY).matches
}

/** 把选择落到 DOM 上。与 dashboard.html 里的预绘制脚本逻辑保持一致。 */
function apply(choice: ThemeChoice): void {
  const dark = choice === 'dark' || (choice === 'system' && systemPrefersDark())
  document.documentElement.classList.toggle('dark', dark)
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
}

export interface ThemeController {
  choice: ThemeChoice
  /** 当前实际生效的是不是暗色（system 时会跟随系统） */
  isDark: boolean
  setChoice: (next: ThemeChoice) => void
  /** 在 light / dark 之间切换；当前是 system 时切到与系统相反的那个 */
  toggle: () => void
}

export function useTheme(): ThemeController {
  const [choice, setChoiceState] = useState<ThemeChoice>(readStored)
  const [isDark, setIsDark] = useState<boolean>(() => {
    const c = readStored()
    return c === 'dark' || (c === 'system' && systemPrefersDark())
  })

  // 选择变化 → 落到 DOM + 存起来
  useEffect(() => {
    apply(choice)
    setIsDark(document.documentElement.classList.contains('dark'))
    try {
      localStorage.setItem(STORAGE_KEY, choice)
    } catch {
      /* 忽略写入失败 */
    }
  }, [choice])

  // 选了 system 时要跟随系统实时变化
  useEffect(() => {
    if (choice !== 'system') return
    const mq = window.matchMedia(DARK_QUERY)
    const onChange = () => {
      apply('system')
      setIsDark(mq.matches)
    }
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [choice])

  // 多标签页同步：另一个标签页改了主题，这个也要跟着变
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== STORAGE_KEY) return
      const next = event.newValue
      if (next === 'light' || next === 'dark' || next === 'system') setChoiceState(next)
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  const setChoice = useCallback((next: ThemeChoice) => setChoiceState(next), [])

  const toggle = useCallback(() => {
    setChoiceState((current) => {
      const currentlyDark =
        current === 'dark' || (current === 'system' && systemPrefersDark())
      return currentlyDark ? 'light' : 'dark'
    })
  }, [])

  return { choice, isDark, setChoice, toggle }
}
