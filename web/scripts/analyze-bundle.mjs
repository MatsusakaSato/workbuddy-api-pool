/**
 * 产物体积分析：回答「这个 chunk 里到底装了什么」。
 *
 * 为什么需要它 —— Rolldown 给自动切出来的共享 chunk 起的名字**取自其中一个成员模块**，
 * 于是会出现名字与内容完全无关的情况：本项目里那个叫 `clipboard-*.js` 的 chunk
 * 里其实一条 clipboard 代码都没有，装的是 react-router + sonner + cn + floating-ui
 * + 一半 react。只看文件名做体积分析一定会得出错误结论（我们已经被误导过一次，
 * 错记成"里面有 ReactDOM"，实际 ReactDOM 在主入口 chunk 里）。
 *
 * 所以这个脚本直接接进构建流程，用 generateBundle 拿真实的 module → chunk 映射，
 * 按包聚合后打印。**它跑一次真实构建**，但写到临时目录，不碰 gateway/static。
 *
 * 用法：
 *   node scripts/analyze-bundle.mjs                 # 总览：每个 chunk 的构成
 *   node scripts/analyze-bundle.mjs usage           # 只看名字含 usage 的 chunk 的全部模块
 *   node scripts/analyze-bundle.mjs --json          # 输出 JSON，便于喂给别的工具
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { build } from 'vite'

const args = process.argv.slice(2)
const asJson = args.includes('--json')
const filter = args.find((a) => !a.startsWith('--'))

const HERE = path.resolve(import.meta.dirname, '..')
const TMP_OUT = fs.mkdtempSync(path.join(os.tmpdir(), 'wbgw-analyze-'))

/**
 * 从模块绝对路径里推断它属于哪个包。
 *
 * 取**最后一个** `node_modules/<包>/` 段，而不是第一个：
 * pnpm 的真实路径是 `…/node_modules/.pnpm/<编码名>@<版本>/node_modules/<真名>/…`，
 * 第一个只会匹到 `.pnpm` 这个伪目录。末尾那个才是真正的包。
 * 这样 `@scope/name`、pnpm 的 `.pnpm` 布局、以及 node_modules 提升后的路径都能正确处理。
 */
function packageOf(id) {
  if (id.includes('/src/')) return null // 本项目源码，单独归类
  const hits = [...id.matchAll(/node_modules\/((?:@[^/]+\/)?[^/]+)\//g)]
    .map((m) => m[1])
    .filter((p) => p !== '.pnpm')
  return hits.length ? hits[hits.length - 1] : '(未知)'
}

/** 把每个 chunk 的模块按包聚合成 { 包名: 字节数 } */
function byPackage(modules) {
  const acc = new Map()
  for (const [id, info] of Object.entries(modules ?? {})) {
    const bytes = info?.renderedLength ?? 0
    const pkg = packageOf(id) ?? '【本项目源码】'
    acc.set(pkg, (acc.get(pkg) ?? 0) + bytes)
  }
  return [...acc].sort((a, b) => b[1] - a[1])
}

const chunks = []
const assets = []

try {
  await build({
    configFile: path.resolve(HERE, 'vite.config.ts'),
    logLevel: 'warn',
    // 覆盖 outDir，避免分析构建污染 gateway/static。
    // vite.config.ts 的 cleanAssetsDir 读的是解析后的 outDir，所以不会误删生产资源。
    build: { outDir: TMP_OUT, emptyOutDir: true },
    plugins: [
      {
        name: 'wbgw-analyze-bundle',
        generateBundle(_options, bundle) {
          for (const [fileName, item] of Object.entries(bundle)) {
            if (item.type === 'asset') {
              const size = typeof item.source === 'string'
                ? Buffer.byteLength(item.source)
                : item.source.length
              assets.push({ fileName, size })
              continue
            }
            chunks.push({
              fileName,
              name: item.name,
              // 必须按**字节**算，不能用 code.length：产物里有中文（UTF-8 下 1 个汉字 3 字节），
              // 用 UTF-16 字符数会系统性低估体积。
              size: Buffer.byteLength(item.code),
              moduleCount: Object.keys(item.modules ?? {}).length,
              isEntry: item.isEntry,
              isDynamicEntry: item.isDynamicEntry,
              imports: item.imports,
              dynamicImports: item.dynamicImports,
              packages: byPackage(item.modules).slice(0, 12),
              modules: Object.entries(item.modules ?? {})
                .map(([id, info]) => ({ id, bytes: info?.renderedLength ?? 0 }))
                .sort((a, b) => b.bytes - a.bytes),
            })
          }
        },
      },
    ],
  })
} finally {
  fs.rmSync(TMP_OUT, { recursive: true, force: true })
}

/* ---------- 算出「首屏要下多少」：入口 chunk + 它静态依赖的传递闭包 ---------- */

const byName = new Map(chunks.map((c) => [c.fileName, c]))
const entry = chunks.find((c) => c.isEntry)

function staticClosure(start) {
  const seen = new Set()
  const queue = [start]
  while (queue.length) {
    const file = queue.shift()
    if (seen.has(file)) continue
    seen.add(file)
    for (const dep of byName.get(file)?.imports ?? []) queue.push(dep)
  }
  return seen
}

const firstLoad = entry ? staticClosure(entry.fileName) : new Set()
// 与 Vite 的构建输出、README 保持同一口径：十进制 kB，不是 KiB。
// （两者差 2.4%，混用会让文档里的数字对不上。）
const kb = (n) => (n / 1000).toFixed(1)

if (asJson) {
  console.log(JSON.stringify({ chunks, assets, firstLoad: [...firstLoad] }, null, 2))
} else if (filter) {
  const hit = chunks.filter((c) => c.fileName.includes(filter))
  if (!hit.length) {
    console.error(`没有名字含 "${filter}" 的 chunk。现有：\n  ${chunks.map((c) => c.fileName).join('\n  ')}`)
    process.exit(1)
  }
  for (const c of hit) {
    console.log(`\n${'═'.repeat(78)}\n  ${c.fileName}  (${kb(c.size)} KB, ${c.moduleCount} 模块)\n${'═'.repeat(78)}`)
    console.log('\n  按包聚合：')
    for (const [pkg, bytes] of c.packages) console.log(`    ${kb(bytes).padStart(8)} KB  ${pkg}`)
    console.log('\n  全部模块：')
    for (const m of c.modules) {
      const short = m.id.replace(/^.*node_modules\/(\.pnpm\/)?/, '').replace(/^.*\/src\//, 'src/')
      console.log(`    ${kb(m.bytes).padStart(8)} KB  ${short}`)
    }
  }
} else {
  console.log(`\n${'═'.repeat(78)}\n  产物构成（构建到临时目录，未改动 gateway/static）\n${'═'.repeat(78)}`)
  for (const c of [...chunks].sort((a, b) => b.size - a.size)) {
    const tag = c.isEntry ? '入口' : c.isDynamicEntry ? '按需' : '共享'
    const eager = firstLoad.has(c.fileName) ? '首屏' : '  · '
    console.log(`\n${c.fileName}`)
    console.log(`  ${kb(c.size).padStart(7)} KB · ${String(c.moduleCount).padStart(3)} 模块 · [${tag}] [${eager}]`)
    for (const [pkg, bytes] of c.packages.slice(0, 4)) console.log(`      ${kb(bytes).padStart(7)} KB  ${pkg}`)
  }

  const eagerBytes = [...firstLoad].reduce((s, f) => s + (byName.get(f)?.size ?? 0), 0)
  const cssBytes = assets
    .filter((a) => a.fileName.endsWith('.css'))
    .reduce((s, a) => s + a.size, 0)

  console.log(`\n${'═'.repeat(78)}`)
  console.log(`  首屏 JS （入口 + 静态依赖闭包）：${kb(eagerBytes)} KB`)
  console.log(`  首屏 CSS（HTML 里的 <link>）    ：${kb(cssBytes)} KB`)
  console.log(`  首屏合计                        ：${kb(eagerBytes + cssBytes)} KB`)
  console.log(`\n  字体（woff2）不计入：它们按需加载且会被浏览器缓存，全量约 143 kB。`)
  console.log(`  这里的数字就是产物的实际字节数（未压缩）。verify-ui.mjs 只断言`)
  console.log(`  加载了哪些资源，不统计字节 —— 要查线上真实传输量得看浏览器 Network。`)
  console.log(`  查单个 chunk 装了什么：node scripts/analyze-bundle.mjs <名字片段>`)
  console.log('═'.repeat(78))
}
