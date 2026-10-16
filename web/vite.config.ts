import fs from 'node:fs'
import path from 'node:path'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const HERE = import.meta.dirname
const STATIC_DIR = path.resolve(HERE, '../gateway/static')

/**
 * 构建前只清掉 gateway/static/assets/，不碰别的。
 *
 * 为什么不用 emptyOutDir:true —— 那会把 gateway/static/ 整个清空，
 * 而产物目录和服务端可能放在同一处的其它静态资源（历史上有 vendor/，
 * 现在至少还有 dashboard.html 本身）必须保留。宁可精确删除，
 * 也不要一次构建就把不该动的东西删了。
 */
function cleanAssetsDir(): Plugin {
  return {
    name: 'wbgw-clean-assets',
    apply: 'build',
    buildStart() {
      const target = path.join(STATIC_DIR, 'assets')
      fs.rmSync(target, { recursive: true, force: true })
    },
  }
}

/**
 * 开发模式下 Vite 直接吐 dashboard.html，**不会**走 gateway/server.py 的
 * `html.replace('"__GATEWAY_KEY__"', ...)`，于是页面里的 key 会是占位符本身，
 * 面板一打开就是 401 —— 这是最容易浪费半小时的坑。
 *
 * 这里补上同样的替换（从 ../.env 读），让 dev 与线上行为一致：
 * 打开 dev server 就是可用的面板，不用手工粘 key。
 * 只作用于 serve，构建产物里占位符原样保留给服务端替换。
 */
function injectDevKey(): Plugin {
  return {
    name: 'wbgw-inject-dev-key',
    apply: 'serve',
    transformIndexHtml(html) {
      let key = process.env.GATEWAY_API_KEY ?? ''
      if (!key) {
        try {
          const env = fs.readFileSync(path.resolve(HERE, '../.env'), 'utf8')
          const m = /^GATEWAY_API_KEY=(.*)$/m.exec(env)
          if (m) key = m[1].trim().replace(/^["']|["']$/g, '')
        } catch {
          /* 没有 .env 就保持占位符，页面上会提示 */
        }
      }
      if (!key) return html
      return html.replace('"__GATEWAY_KEY__"', JSON.stringify(key))
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), cleanAssetsDir(), injectDevKey()],

  /* 服务端把静态资源挂在 /static/* 下（gateway/server.py 的 _static()），
     所以产物的资源引用必须是 /static/assets/xxx，不能是默认的 /assets/xxx。 */
  base: '/static/',

  resolve: {
    alias: { '@': path.resolve(HERE, 'src') },
  },

  build: {
    outDir: STATIC_DIR,
    emptyOutDir: false,
    /* 入口是 dashboard.html 而不是 index.html —— 服务端读的就是
       gateway/static/dashboard.html，这样服务端一行都不用改。 */
    rollupOptions: {
      input: path.resolve(HERE, 'dashboard.html'),
      output: {
        /* 用哈希文件名，配合服务端 /static/* 的 max-age=86400 做内容寻址缓存。
           内容一变文件名就变，所以不会串版本。 */
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
    chunkSizeWarningLimit: 700,
  },

  server: {
    port: 5173,
    /* 开发时由 Vite 提供 HMR，API 请求代理到真实网关。
       浏览器视角仍同源，因此不需要 CORS。 */
    proxy: {
      '/admin': { target: 'http://127.0.0.1:8788', changeOrigin: false },
      '/v1': { target: 'http://127.0.0.1:8788', changeOrigin: false },
      '/healthz': { target: 'http://127.0.0.1:8788', changeOrigin: false },
    },
  },
})
