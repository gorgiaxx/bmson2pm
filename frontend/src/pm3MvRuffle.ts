export interface RufflePlayerApi {
  load(options: Record<string, unknown>): Promise<void>
  callExternalInterface(name: string, ...args: unknown[]): unknown
  resume(): void
  suspend(): void
  readonly isPlaying: boolean
}

export interface RufflePlayerElement extends HTMLElement {
  ruffle(): RufflePlayerApi
  MVLoad?: (mvId: number) => unknown
  MVState?: (state: number) => unknown
  MVHeavy?: () => unknown
  MVCont?: (enter: boolean) => unknown
}

interface RuffleSource {
  createPlayer(): RufflePlayerElement
}

interface RuffleRegistry {
  config?: Record<string, unknown>
  newest?: () => RuffleSource | null
}

declare global {
  interface Window {
    RufflePlayer?: RuffleRegistry
  }
}

let runtimePromise: Promise<RuffleSource> | null = null

function loadRuntime(): Promise<RuffleSource> {
  if (runtimePromise) return runtimePromise
  const next = new Promise<RuffleSource>((resolve, reject) => {
    const current = window.RufflePlayer
    const source = current?.newest?.()
    if (source) {
      resolve(source)
      return
    }

    const registry = current ?? {}
    registry.config = {
      ...registry.config,
      publicPath: '/ruffle/',
      polyfills: false,
    }
    window.RufflePlayer = registry

    const existing = document.querySelector<HTMLScriptElement>('script[data-pm3-ruffle]')
    const script = existing ?? document.createElement('script')
    const loaded = () => {
      const newest = window.RufflePlayer?.newest?.()
      if (newest) resolve(newest)
      else reject(new Error('Ruffle 运行时未注册播放器'))
    }
    const failed = () => reject(new Error('Ruffle 运行时加载失败'))
    script.addEventListener('load', loaded, { once: true })
    script.addEventListener('error', failed, { once: true })
    if (!existing) {
      script.src = '/ruffle/ruffle.js'
      script.async = true
      script.dataset.pm3Ruffle = 'true'
      document.head.appendChild(script)
    }
  }).catch((error: unknown) => {
    runtimePromise = null
    throw error
  })
  runtimePromise = next
  return next
}

export async function createPm3MvPlayer(): Promise<RufflePlayerElement> {
  const source = await loadRuntime()
  return source.createPlayer()
}
