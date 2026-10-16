"use client"

import { Toaster as Sonner, type ToasterProps } from "sonner"
import { CircleCheckIcon, InfoIcon, TriangleAlertIcon, OctagonXIcon, Loader2Icon } from "lucide-react"
import { useTheme } from "@/hooks/use-theme"

/**
 * 对预设做的一处修改：主题来源从 next-themes 换成项目自己的 useTheme。
 *
 * 原来的写法是 `useTheme()` from "next-themes"，但本项目**没有挂载**
 * next-themes 的 ThemeProvider —— 主题是自己实现的（见 hooks/use-theme.ts，
 * 因为必须和 dashboard.html 里那段预绘制脚本共用同一套 localStorage 约定）。
 * 于是 useTheme() 返回空对象，theme 永远落到默认的 "system"：
 * 系统是浅色而你把面板切成深色时，**吐司仍然是浅色的**。
 *
 * 现在直接读自己的主题状态，吐司与面板永远一致，next-themes 也可以彻底移除了。
 */
const Toaster = ({ ...props }: ToasterProps) => {
  const { isDark } = useTheme()

  return (
    <Sonner
      theme={isDark ? "dark" : "light"}
      className="toaster group"
      icons={{
        success: (
          <CircleCheckIcon className="size-4" />
        ),
        info: (
          <InfoIcon className="size-4" />
        ),
        warning: (
          <TriangleAlertIcon className="size-4" />
        ),
        error: (
          <OctagonXIcon className="size-4" />
        ),
        loading: (
          <Loader2Icon className="size-4 animate-spin" />
        ),
      }}
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
          "--border-radius": "var(--radius)",
        } as React.CSSProperties
      }
      toastOptions={{
        classNames: {
          toast: "cn-toast",
        },
      }}
      {...props}
    />
  )
}

export { Toaster }
