/**
 * 采集本机网关数据 → docs/.viz-data.json → 重建对比页。
 *
 * 为什么要有这个脚本：对比页里的图形必须用**真实数据**渲染，
 * 否则"信息密度"这类判断就没有依据。数据是快照，会随网关运行变化，
 * 所以不能手写进 HTML —— 必须每次重新采集。
 *
 * 三个文件的分工（改动前先看这里）：
 *   · app.jsx      —— 对比页的渲染逻辑（源）。改了它要跑 build.mjs 重新打包。
 *   · template.html —— 页面骨架，含两个占位标记 __VIZ_DATA__ / __VIZ_APP__。
 *   · bundle.js    —— build.mjs 的产物；本脚本把它和数据一起塞进模板。
 *
 * 用法：node docs/.viz-build/refresh.mjs
 * 前置：网关在 127.0.0.1:8788 运行，且 .env 里有 API_KEY；
 *       若改过 app.jsx，先跑 node docs/.viz-build/build.mjs。
 */

import { readFileSync, writeFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '../..')
const BASE = process.env.GATEWAY_URL ?? 'http://127.0.0.1:8788'

/** 从 .env 读 API_KEY —— 面板的密钥就是这么来的，不额外引入 dotenv */
function readKey() {
  const text = readFileSync(resolve(ROOT, '.env'), 'utf8')
  const m = /API_KEY\s*=\s*"?([^"\n]+)"?/.exec(text)
  if (!m) throw new Error('.env 里没有找到 API_KEY')
  return m[1]
}

const KEY = readKey()

async function get(path) {
  const res = await fetch(BASE + path, { headers: { Authorization: `Bearer ${KEY}` } })
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`)
  return res.json()
}

console.log(`采集 ${BASE} …`)
const [snapshot, usage, log, models] = await Promise.all([
  get('/admin/status'),
  get('/admin/usage?days=90'),
  get('/admin/log?limit=300'),
  get('/admin/models'),
])

const data = {
  snapshot,
  usage,
  log,
  models: models.data,
  names: Object.fromEntries(snapshot.accounts.map((a) => [a.uin, a.nickname])),
}

writeFileSync(resolve(HERE, '../.viz-data.json'), JSON.stringify(data))

/* 把数据块与打包后的 bundle 一起写进最终 HTML。
   数据必须内联：对比页要能单文件打开、不依赖网关在跑。

   用**占位标记**替换，而不是靠"找出第 N 块 script"的下标算术 ——
   后者在替换一次之后偏移就变了，必须重新扫描，既易错又难读。
   模板里的标记形如 <script>/*__VIZ_DATA__*​/</script>。 */
const tpl = readFileSync(resolve(HERE, 'template.html'), 'utf8')
const bundle = readFileSync(resolve(HERE, 'bundle.js'), 'utf8')

const DATA_MARK = '<script>/*__VIZ_DATA__*/</script>'
const APP_MARK = '<script>/*__VIZ_APP__*/</script>'
for (const [mark, name] of [
  [DATA_MARK, '数据'],
  [APP_MARK, '应用'],
]) {
  if (!tpl.includes(mark)) throw new Error(`模板缺少${name}占位符，请对照 template.html`)
}

/* ⚠️ 必须用**函数形式**的替换，不能传字符串。
   String.replace 会把替换文本里的 `$&`、`$'`、`$\`` 当成特殊模式展开 ——
   注入的 JSON / bundle 里只要出现 `$`，替换结果就会被悄悄改写
   （踩过一次：产物里留下了半截占位符）。函数形式不做任何模式展开。 */
let out = tpl
  .replace(DATA_MARK, () => '<script>window.__VIZ__ = ' + JSON.stringify(data) + ';</script>')
  .replace(APP_MARK, () => '<script>' + bundle + '</script>')

// 自检：结构完整才写盘，避免产出一个残缺的 HTML
for (const tag of ['<body>', '</body>', 'id="baseline-accounts"', 'id="gauge-bad"', 'createRoot']) {
  if (!out.includes(tag)) throw new Error(`产出缺少 ${tag}`)
}
if (out.includes('__VIZ_DATA__') || out.includes('__VIZ_APP__')) {
  throw new Error('产出仍残留占位符')
}
// bundle 里若出现字面量 </script>，会把 HTML 提前截断（真踩过一次）。
// 内联脚本里出现它是非法的，这里直接拦住。
if (bundle.includes('</script>')) throw new Error('bundle 含字面量 </script>，无法安全内联')

writeFileSync(resolve(HERE, '../可视化方案对比.html'), out)

console.log(`账号 ${snapshot.accounts.length} · 模型 ${models.data.length} · 用量日 ${usage.days.length} · 日志 ${log.items.length}`)
console.log('已写出 docs/可视化方案对比.html 与 docs/.viz-data.json')
