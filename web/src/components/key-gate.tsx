/**
 * 密钥闸门。
 *
 * 什么时候会出现：
 *  · 注入失效 —— 页面不是经网关提供的（比如直接双击打开了 HTML 文件），
 *    此时 window.__DSH_GATEWAY_KEY__ 还是占位符；
 *  · key 对不上 —— 服务端换过 key（改了 .env 重启），而浏览器里的旧 key 还留着，
 *    此时所有 /admin 请求都返回 401。
 *
 * 这两种情况都必须给出可操作的出路，而不是留一个空白或 401 循环。
 * 手工填的 key 存 localStorage，并在有注入 key 时作为**兜底**而不是覆盖。
 */

import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { KeyRound, ShieldAlert } from 'lucide-react'
import { clearKey, injectedKey, maskKey, saveKey } from '@/lib/key'

export function KeyGate({
  /** 触发原因：没有 key，还是有 key 但被拒 */
  reason,
  /** 服务端给的具体错误信息（401 时有） */
  serverMessage,
}: {
  reason: 'missing' | 'rejected'
  serverMessage?: string
}) {
  const qc = useQueryClient()
  const [value, setValue] = useState('')
  const injected = injectedKey()

  const submit = (): void => {
    saveKey(value)
    // 换了 key，之前所有查询结果都作废
    void qc.invalidateQueries()
  }

  return (
    <div className="flex min-h-svh items-center justify-center p-6">
      <div className="w-full max-w-md">
        <div className="flex items-start gap-3">
          <span
            className={
              reason === 'missing'
                ? 'mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-warning-subtle text-warning'
                : 'mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md bg-danger-subtle text-danger'
            }
          >
            {reason === 'missing' ? (
              <KeyRound className="size-4" />
            ) : (
              <ShieldAlert className="size-4" />
            )}
          </span>

          <div className="min-w-0 flex-1">
            <h1 className="text-lg font-semibold tracking-tight">
              {reason === 'missing' ? '未取得 API Key' : 'API Key 不正确'}
            </h1>

            <p className="mt-1 text-sm text-muted-foreground">
              {reason === 'missing' ? (
                <>
                  页面里没有注入密钥。正常通过{' '}
                  <code className="font-mono text-xs">http://127.0.0.1:8788/</code> 打开面板时，
                  服务端会自动注入，不需要手工填。
                </>
              ) : (
                <>
                  服务端拒绝了当前密钥
                  {serverMessage ? `（${serverMessage}）` : ''}。
                  多半是 <code className="font-mono text-xs">.env</code> 里的 key 变过，
                  而浏览器里还留着旧值。
                </>
              )}
            </p>

            {injected ? (
              <div className="mt-3 rounded-md border border-border bg-muted px-3 py-2 text-2xs">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-muted-foreground">
                    当前页面注入的 key：<code className="font-mono">{maskKey(injected)}</code>
                  </span>
                  <Button
                    size="xs"
                    variant="ghost"
                    onClick={() => {
                      clearKey()
                      void qc.invalidateQueries()
                    }}
                  >
                    清除手工填的
                  </Button>
                </div>
              </div>
            ) : null}

            <form
              className="mt-4 space-y-3"
              onSubmit={(e) => {
                e.preventDefault()
                submit()
              }}
            >
              <div className="space-y-1.5">
                <Label htmlFor="gateway-key" className="text-xs">
                  API Key
                </Label>
                <Input
                  id="gateway-key"
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  className="font-mono"
                  placeholder="sk-wb-..."
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                />
              </div>
              <Button type="submit" className="w-full" disabled={!value.trim()}>
                保存并重试
              </Button>
            </form>

            <div className="mt-4 space-y-1 text-2xs text-muted-foreground">
              <p>
                完整 key 在项目根目录的 <code className="font-mono">.env</code>：
              </p>
              <pre className="scroll-thin overflow-x-auto rounded-md bg-code-bg p-2 font-mono">
                grep GATEWAY_API_KEY .env
              </pre>
              <p>也可以用深链带进来（写入后会自动从地址栏抹掉）：</p>
              <pre className="scroll-thin overflow-x-auto rounded-md bg-code-bg p-2 font-mono">
                http://127.0.0.1:8788/?key=&lt;你的KEY&gt;
              </pre>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
