/**
 * 接入页。
 *
 * 这一页只有一个目标：让用户**在 30 秒内把客户端接上**。所以：
 *  · 连接信息用定义列表（dl）而不是表格 —— 这是"键值对"语义，不是数据表；
 *  · 每个片段自带复制按钮，且片段里的 base_url / key / model 都是**真实值**，
 *    粘出去就能跑（不是 `your-api-key-here` 那种还需要手工替换的占位）；
 *  · 提供「隐藏密钥」开关：默认显示（本地自用），投屏/截图时一键遮住。
 *    注意遮住的只是**显示文本**，复制出来的始终是真实值 —— 否则这个开关
 *    会让复制功能失效，用户会以为是 bug。
 *  · model 从实时目录里挑一个存在的，避免粘出去就报 model not found。
 */

import { useMemo, useState } from 'react'
import { Eye, EyeOff } from 'lucide-react'
import { Page } from '@/components/page'
import { Panel } from '@/components/panel'
import { EmptyState } from '@/components/empty-state'
import { CopyButton } from '@/components/copy-button'
import { StatusChip } from '@/components/status-dot'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  CodeBlock,
  CodeBlockActions,
  CodeBlockContent,
  CodeBlockCopyButton,
  CodeBlockHeader,
  CodeBlockTitle,
} from '@/components/ui/code-block'
import { HighlightedCode } from '@/components/highlighted-code'
import { useModels, useSettings } from '@/hooks/use-gateway'
import { resolveKey, maskKey } from '@/lib/key'
import { formatInt } from '@/lib/format'
import { cn } from '@/lib/utils'

/** 从实时目录里挑一个"最值得用来试"的模型，保证片段可直接运行 */
function pickModel(ids: string[]): string {
  const preferred = ['deepseek-v4-pro', 'deepseek-v4-flash', 'deepseek-v4', 'hy3']
  for (const id of preferred) if (ids.includes(id)) return id
  return ids[0] ?? 'deepseek-v4-flash'
}

function Field({
  label,
  children,
  hint,
}: {
  label: string
  children: React.ReactNode
  hint?: string
}) {
  return (
    <>
      <dt className="py-1.5 text-2xs text-muted-foreground">{label}</dt>
      <dd className="flex min-w-0 items-center gap-1.5 py-1.5">
        <span className="min-w-0 flex-1 truncate text-xs">{children}</span>
        {hint ? <span className="shrink-0 text-2xs text-muted-foreground">{hint}</span> : null}
      </dd>
    </>
  )
}

function Snippet({
  title,
  code,
  /** 复制用的真实内容；未提供时与 code 相同 */
  copyValue,
  language,
  /**
   * 高亮用的语法。默认跟 language 一致。
   * 分开是因为 language 在这里兼作**给用户的文件类型提示**（"存成 .ts"），
   * 而语法选择该按代码的实际内容来 —— 两者不一定同一个答案。
   */
  highlightAs,
}: {
  title: string
  code: string
  copyValue?: string
  language?: string
  highlightAs?: string
}) {
  return (
    <CodeBlock size="sm" className="bg-code-bg">
      <CodeBlockHeader>
        <CodeBlockTitle>{title}</CodeBlockTitle>
        {language ? (
          <span className="font-mono text-2xs text-muted-foreground">{language}</span>
        ) : null}
        <CodeBlockActions>
          <CodeBlockCopyButton value={copyValue ?? code} />
        </CodeBlockActions>
      </CodeBlockHeader>
      <CodeBlockContent>
        <HighlightedCode code={code} language={highlightAs ?? language} />
      </CodeBlockContent>
    </CodeBlock>
  )
}

export function ConnectPage() {
  const settings = useSettings()
  const models = useModels()
  const [revealed, setRevealed] = useState(false)

  const key = resolveKey()
  const baseUrl = settings.data?.base_url ?? ''
  const model = useMemo(
    () => pickModel((models.data?.data ?? []).map((m) => m.id)),
    [models.data],
  )

  /** 显示用与复制用的 key：前者随开关变化，后者永远是真实值 */
  const shownKey = revealed ? key : maskKey(key)

  const snippets = useMemo(() => {
    const k = key || 'sk-wb-…'
    const shown = shownKey || 'sk-wb-…'
    return {
      curl: {
        shown:
          `curl -N ${baseUrl}/chat/completions \\\n` +
          `  -H "Authorization: Bearer ${shown}" \\\n` +
          `  -H 'Content-Type: application/json' \\\n` +
          `  -d '{"model":"${model}","stream":true,"messages":[{"role":"user","content":"你好"}]}'`,
        real:
          `curl -N ${baseUrl}/chat/completions \\\n` +
          `  -H "Authorization: Bearer ${k}" \\\n` +
          `  -H 'Content-Type: application/json' \\\n` +
          `  -d '{"model":"${model}","stream":true,"messages":[{"role":"user","content":"你好"}]}'`,
      },
      python: {
        shown: `from openai import OpenAI

client = OpenAI(base_url="${baseUrl}", api_key="${shown}")

stream = client.chat.completions.create(
    model="${model}",
    messages=[{"role": "user", "content": "你好"}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="")`,
        real: `from openai import OpenAI

client = OpenAI(base_url="${baseUrl}", api_key="${k}")

stream = client.chat.completions.create(
    model="${model}",
    messages=[{"role": "user", "content": "你好"}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="")`,
      },
      node: {
        shown: `import OpenAI from "openai";

const client = new OpenAI({ baseURL: "${baseUrl}", apiKey: "${shown}" });

const stream = await client.chat.completions.create({
  model: "${model}",
  messages: [{ role: "user", content: "你好" }],
  stream: true,
});
for await (const chunk of stream) {
  process.stdout.write(chunk.choices[0]?.delta?.content ?? "");
}`,
        real: `import OpenAI from "openai";

const client = new OpenAI({ baseURL: "${baseUrl}", apiKey: "${k}" });

const stream = await client.chat.completions.create({
  model: "${model}",
  messages: [{ role: "user", content: "你好" }],
  stream: true,
});
for await (const chunk of stream) {
  process.stdout.write(chunk.choices[0]?.delta?.content ?? "");
}`,
      },
      generic: {
        shown: `API 地址   ${baseUrl}\nAPI 密钥   ${shown}\n模型       ${model}`,
        real: `API 地址   ${baseUrl}\nAPI 密钥   ${k}\n模型       ${model}`,
      },
    }
  }, [baseUrl, key, shownKey, model])

  const s = settings.data

  if (settings.isLoading && !s) {
    return (
      <Page title="接入">
        <Skeleton className="h-64 rounded-lg" />
        <Skeleton className="h-48 rounded-lg" />
      </Page>
    )
  }

  if (!s) {
    return (
      <Page title="接入">
        <Panel>
          <EmptyState
            tone="danger"
            title="读不到连接配置"
            description={settings.error?.message ?? '请确认网关在运行，然后重试。'}
            action={
              <Button size="xs" variant="outline" onClick={() => void settings.refetch()}>
                重试
              </Button>
            }
          />
        </Panel>
      </Page>
    )
  }

  return (
    <Page
      title="接入"
    >
      <Panel
        title="连接信息"
        actions={
          <Button
            size="xs"
            variant="ghost"
            onClick={() => setRevealed((v) => !v)}
            aria-pressed={revealed}
          >
            {revealed ? <EyeOff /> : <Eye />}
            {revealed ? '隐藏密钥' : '显示密钥'}
          </Button>
        }
      >
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4">
          <Field label="Base URL">
            <span className="font-mono">{s.base_url}</span>
            <CopyButton value={s.base_url} label="复制 Base URL" className="ml-1 inline-flex" />
          </Field>

          <Field label="API Key">
            <span className={cn('font-mono', !revealed && 'text-muted-foreground')}>
              {shownKey || '（未取得）'}
            </span>
            {key ? (
              <CopyButton value={key} label="复制完整 API Key" className="ml-1 inline-flex" />
            ) : null}
          </Field>

          <Field label="监听地址">
            <span className="font-mono">
              {s.host}:{s.port}
            </span>
            <StatusChip tone="warning" className="ml-1.5">
              仅本机
            </StatusChip>
          </Field>

          <Field label="账号池" hint="可用 / 总数">
            <span className="tnum">
              {s.accounts.available}/{s.accounts.total}
              {s.accounts.cooling > 0 ? `（冷却 ${s.accounts.cooling}）` : ''}
              {s.accounts.exhausted > 0 ? `（耗尽 ${s.accounts.exhausted}）` : ''}
            </span>
          </Field>

          <Field label="模型数">
            <span className="tnum">{formatInt(s.models_total)}</span>
          </Field>

          <Field label="Gemini 原生接口">
            {s.gemini.configured ? (
              <>
                <span>{s.gemini.name}</span>
                <span className="ml-1 font-mono text-2xs text-muted-foreground">{s.gemini.base}</span>
              </>
            ) : (
              <span className="text-muted-foreground">未配置</span>
            )}
          </Field>

          <Field label="状态目录">
            <span className="font-mono text-2xs text-muted-foreground">{s.paths.state}</span>
            <CopyButton
              value={s.paths.state}
              label="复制状态目录"
              className="ml-1 inline-flex"
            />
          </Field>

          <Field label="面板地址">
            <span className="font-mono text-2xs text-muted-foreground">{s.dashboard_url}</span>
            <CopyButton
              value={s.dashboard_url}
              label="复制面板地址"
              className="ml-1 inline-flex"
            />
          </Field>
        </dl>
      </Panel>

      <div className="grid gap-4 xl:grid-cols-2">
        <Snippet
          title="curl"
          language="shell"
          code={snippets.curl.shown}
          copyValue={snippets.curl.real}
        />
        <Snippet
          title="Python（OpenAI SDK）"
          language="python"
          code={snippets.python.shown}
          copyValue={snippets.python.real}
        />
        <Snippet
          title="Node / TypeScript（OpenAI SDK）"
          language="ts"
          // 这段代码里一个类型标注都没有（import / const / await / for-await），
          // 所以用 javascript 语法高亮**结果完全一样**，但能省掉 177 KB 的
          // typescript 语法包（它比 javascript 语法大，且只在有标注时才有差别）。
          highlightAs="javascript"
          code={snippets.node.shown}
          copyValue={snippets.node.real}
        />
        <Snippet
          title="任意客户端"
          code={snippets.generic.shown}
          copyValue={snippets.generic.real}
        />
      </div>
    </Page>
  )
}
