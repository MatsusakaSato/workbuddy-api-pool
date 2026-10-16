/**
 * 入口。
 *
 * 顺序很重要：**先翻译旧深链，再渲染**。
 * translateLegacyDeepLink() 会把 ?tab=… 改写成 #/…，如果先渲染 HashRouter，
 * 它会先读到空 hash 并跳一次概览，然后才被改写 —— 用户会看到一次多余的跳转闪动。
 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { App } from './app'
import { translateLegacyDeepLink } from './lib/legacy-deeplink'

translateLegacyDeepLink()

const container = document.getElementById('root')
if (!container) throw new Error('找不到 #root 挂载点')

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
