import fs from 'node:fs'
import path from 'node:path'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const HERE = import.meta.dirname
const STATIC_DIR = path.resolve(HERE, '../gateway/static')

/**
 * 构建前只清掉产物目录下的 assets/，不碰别的。
 *
 * 为什么不用 emptyOutDir:true —— 那会把 gateway/static/ 整个清空，
 * 而产物目录里还有不该由构建决定生死的东西（至少 dashboard.html 本身）。
 * 宁可精确删除，也不要一次构建就把不该动的东西删了。
 *
 * 注意这里的目标取自 **解析后的 outDir**，不是写死的 STATIC_DIR：
 * scripts/analyze-bundle.mjs 会用临时的 outDir 跑一次分析构建，
 * 如果写死 STATIC_DIR，那次分析就会把真正的生产资源删掉。
 */
function cleanAssetsDir(): Plugin {
  let assetsDir = ''
  return {
    name: 'wbgw-clean-assets',
    apply: 'build',
    configResolved(config) {
      assetsDir = path.join(config.build.outDir, 'assets')
    },
    buildStart() {
      if (assetsDir) fs.rmSync(assetsDir, { recursive: true, force: true })
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
 *
 * 顺带修一个入口文件名带来的小坑，见下面 configureServer 的注释。
 */
function injectDevKey(): Plugin {
  return {
    name: 'wbgw-inject-dev-key',
    apply: 'serve',

    /**
     * Vite dev 默认把 `/` 重定向到 `base`（这里是 `/static/`），并把 `index.html`
     * 当作目录首页 —— 但本项目的入口叫 **dashboard.html**（服务端读的就是这个文件名，
     * 不能改）。于是它打印的 `http://localhost:5173/static/` 实际是 **404**，
     * 开发者照着点会一脸问号；这台机器上 5173 还恰好被另一个不相干的应用
     * （睿云智能工作台）占着，更容易误判成"面板坏了"。
     *
     * 这里在 Vite 自己的中间件之前把目录请求改写到真正的入口，
     * 让 `pnpm run dev` 打印出来的地址直接可用，也和线上
     * （server.py 把 `/` 映射到 dashboard.html）保持一致。
     */
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        const url = req.url ?? ''
        if (url === '/' || url === '/static' || url === '/static/') {
          req.url = '/static/dashboard.html'
        }
        next()
      })
    },

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
    /* 显式绑 IPv4 回环。
       不写这一行时 Vite 默认绑 IPv6 的 [::1]，而另一个进程可能正占着 IPv4 的
       同一端口 —— 两个进程各占一个协议族，谁也不报错，Vite 打印"5173 可用"，
       但你浏览器访问 127.0.0.1:5173 看到的**是别人的应用**。
       （本机上确实如此：睿云智能工作台的 srtclaw-service 占着 IPv4:5173。）
       绑死 IPv4 后端口冲突会被真正检测到，Vite 会自动退到 5174 并明确打印出来。
       顺带也更安全：只监听回环，不对外暴露。 */
    host: '127.0.0.1',
    /* 开发时由 Vite 提供 HMR，API 请求代理到真实网关。
       浏览器视角仍同源，因此不需要 CORS。 */
    proxy: {
      '/admin': { target: 'http://127.0.0.1:8788', changeOrigin: false },
      '/v1': { target: 'http://127.0.0.1:8788', changeOrigin: false },
      '/healthz': { target: 'http://127.0.0.1:8788', changeOrigin: false },
    },
  },
})
