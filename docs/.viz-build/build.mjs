import { rolldown } from '/Users/amano/WorkSpace/反代api号池/web/node_modules/.pnpm/rolldown@1.2.14/node_modules/rolldown/dist/index.mjs'
import { writeFileSync } from 'node:fs'

const WEB = '/Users/amano/WorkSpace/反代api号池/web'

// 原型只依赖 react / react-dom —— 两者在顶层 node_modules 已链接。
// 刻意不引入 recharts：这些可视化的目的之一就是证明"不需要图表库也能做"。
const bundle = await rolldown({
  input: '/Users/amano/WorkSpace/反代api号池/docs/.viz-build/app.jsx',
  platform: 'browser',
  resolve: {
    modules: [`${WEB}/node_modules`],
    // react-dom 的 cjs 构建用 require("scheduler")，pnpm 下未链接到顶层，
    // 不别名的话会被当 external，页面直接报 "scheduler is not defined"。
    alias: { scheduler: `${WEB}/node_modules/.pnpm/scheduler@0.28.0/node_modules/scheduler` },
  },
  transform: { jsx: { runtime: 'automatic' } },
})

const { output } = await bundle.generate({ format: 'iife', minify: true })
const code = output[0].code

const leftovers = ['immer', 'react-redux', 'recharts', 'tiny-invariant', 'es-toolkit', 'scheduler']
  .filter(n => new RegExp(`(require\\(["']${n}|from\\s*["']${n})`).test(code))
if (leftovers.length) {
  console.error('!! 未内联的依赖:', leftovers.join(', '))
  process.exit(1)
}
writeFileSync('/Users/amano/WorkSpace/反代api号池/docs/.viz-build/bundle.js', code)
console.log('bundled ok, bytes =', code.length)
