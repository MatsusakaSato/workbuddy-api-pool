"use client"

import { Toaster as Sonner, type ToasterProps } from "sonner"
import { CircleCheckIcon, InfoIcon, TriangleAlertIcon, OctagonXIcon, Loader2Icon } from "lucide-react"

/**
 * 对预设做的一处修改：`theme` 从"跟随主题"改成写死 "light"。
 *
 * 上游 shadcn 版本用 `useTheme()` from "next-themes"，但本项目从来没有挂载
 * next-themes 的 ThemeProvider，所以那个 hook 返回空对象、theme 会落到
 * 默认的 "system" —— 结果是吐司的颜色跟着**操作系统**走，而不是跟着面板走。
 * 这个问题在还有主题切换时是实打实的 bug；现在面板固定浅色，
 * 唯一的正确取值就是 "light"，不再需要任何 hook。
 */
const Toaster = ({ ...props }: ToasterProps) => {
  return (
    <Sonner
      theme="light"
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
