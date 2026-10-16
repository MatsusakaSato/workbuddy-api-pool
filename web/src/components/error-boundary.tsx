/**
 * 错误边界。
 *
 * 一个本地工具最忌讳的是"白屏"——用户不知道发生了什么，也没法自己恢复。
 * 所以这里做三件事：
 *  1. 显示错误摘要（错误信息 + 组件栈的前几行），不藏着；
 *  2. 给出「重试」与「重载页面」两个动作；
 *  3. 提示状态文件位置，让人知道去哪看日志。
 */

import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { AlertTriangle } from 'lucide-react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
  componentStack: string
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, componentStack: '' }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 控制台留一份完整栈，方便排查（生产环境也一样，这是本地工具）
    console.error('[控制台] 渲染异常', error, info.componentStack)
    this.setState({ componentStack: info.componentStack ?? '' })
  }

  private reset = (): void => {
    this.setState({ error: null, componentStack: '' })
  }

  render(): ReactNode {
    const { error, componentStack } = this.state
    if (!error) return this.props.children

    return (
      <div className="flex min-h-svh items-center justify-center p-6">
        <div className="w-full max-w-xl">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-danger-subtle text-danger">
              <AlertTriangle className="size-4" />
            </span>
            <div className="min-w-0 flex-1">
              <h1 className="text-lg font-semibold tracking-tight">界面出错了</h1>
              <p className="mt-1 text-sm text-muted-foreground">
                这是前端渲染异常，网关服务本身通常没事。可以先重试；不行就重载页面。
              </p>

              <pre className="scroll-thin mt-4 max-h-48 overflow-auto rounded-md bg-code-bg p-3 font-mono text-xs leading-5 text-foreground">
                {error.message}
                {componentStack.trim() ? `\n${componentStack.trim().split('\n').slice(0, 6).join('\n')}` : ''}
              </pre>

              <div className="mt-4 flex items-center gap-2">
                <Button size="sm" onClick={this.reset}>
                  重试
                </Button>
                <Button size="sm" variant="outline" onClick={() => window.location.reload()}>
                  重载页面
                </Button>
              </div>

              <p className="mt-4 text-2xs text-muted-foreground">
                服务端日志：<code className="font-mono">state/gateway.log</code> ·
                若服务已停，重新运行 <code className="font-mono">./run.sh</code>
              </p>
            </div>
          </div>
        </div>
      </div>
    )
  }
}
