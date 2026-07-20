import {
  CirclePause,
  CirclePlay,
  RefreshCw,
  Radio,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  createPm3MvPlayer,
  type RufflePlayerApi,
  type RufflePlayerElement,
} from '../pm3MvRuffle'

interface Pm3MvPreviewProps {
  projectId: string
  mvId: number
  available: boolean
}

const MV_STATES = ['LOW', 'MIDDLE', 'HIGH', 'FULL'] as const
const DEFAULT_MV_STATE = 3

function mvUrl(projectId: string, mvId: number, state: number, previewKey: number): string {
  return `/api/projects/${encodeURIComponent(projectId)}/pm3/mv-preview/mv/mv${mvId}.swf?state=${MV_STATES[state].toLowerCase()}&preview=${previewKey}`
}

function loadOptions(url: string): Record<string, unknown> {
  return {
    url,
    allowNetworking: 'internal',
    allowScriptAccess: true,
    autoplay: 'on',
    backgroundColor: '#000000',
    contextMenu: 'off',
    splashScreen: false,
    warnOnUnsupportedContent: false,
    showSwfDownload: false,
    quality: 'high',
    scale: 'showAll',
    wmode: 'opaque',
  }
}

async function loadPlayer(
  element: RufflePlayerElement,
  player: RufflePlayerApi,
  options: Record<string, unknown>,
): Promise<void> {
  let timeout = 0
  let onLoaded = () => undefined
  const loaded = new Promise<void>((resolve) => {
    onLoaded = () => {
      window.clearTimeout(timeout)
      resolve()
    }
    element.addEventListener('loadeddata', onLoaded, { once: true })
    timeout = window.setTimeout(resolve, 3000)
  })
  try {
    await player.load(options)
    await loaded
  } finally {
    window.clearTimeout(timeout)
    element.removeEventListener('loadeddata', onLoaded)
  }
}

export function Pm3MvPreview({ projectId, mvId, available }: Pm3MvPreviewProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const elementRef = useRef<RufflePlayerElement | null>(null)
  const apiRef = useRef<RufflePlayerApi | null>(null)
  const reloadRef = useRef(0)
  const stateLoadRef = useRef(0)
  const directLoadRef = useRef(0)
  const playingRef = useRef(true)
  const [reloadRevision, setReloadRevision] = useState(0)
  const [status, setStatus] = useState<'loading' | 'direct' | 'error'>('loading')
  const [error, setError] = useState('')
  const [playing, setPlaying] = useState(true)
  const [state, setState] = useState(DEFAULT_MV_STATE)

  const mountDirectPlayer = useCallback(async (
    nextState: number,
    revision: number,
    replayColdLoad = false,
  ): Promise<boolean> => {
    const host = hostRef.current
    if (!host || revision !== reloadRef.current) return false

    // Ruffle does not reliably replace one PM3 SWF with another in the same
    // player. A fresh instance is required for both MV and state changes.
    const directRevision = ++directLoadRef.current
    const element = await createPm3MvPlayer()
    if (revision !== reloadRef.current || directRevision !== directLoadRef.current) return false

    element.style.width = '100%'
    element.style.height = '100%'
    const player = element.ruffle()
    apiRef.current?.suspend()
    host.replaceChildren(element)
    elementRef.current = element
    apiRef.current = player

    try {
      await loadPlayer(element, player, loadOptions(
        mvUrl(projectId, mvId, nextState, ++stateLoadRef.current),
      ))
      if (revision !== reloadRef.current || directRevision !== directLoadRef.current) {
        player.suspend()
        return false
      }

      if (replayColdLoad) {
        // The first SWF loaded by a cold WASM runtime can report loadeddata
        // before its initial frame is painted. A short replay makes it visible.
        await new Promise((resolve) => window.setTimeout(resolve, 150))
        if (revision !== reloadRef.current || directRevision !== directLoadRef.current) {
          player.suspend()
          return false
        }
        await loadPlayer(element, player, loadOptions(
          mvUrl(projectId, mvId, nextState, ++stateLoadRef.current),
        ))
      }

      if (revision !== reloadRef.current || directRevision !== directLoadRef.current) {
        player.suspend()
        return false
      }
      if (!playingRef.current) player.suspend()
      return true
    } catch (reason) {
      if (revision !== reloadRef.current || directRevision !== directLoadRef.current) return false
      player.suspend()
      if (elementRef.current === element) {
        elementRef.current = null
        apiRef.current = null
        element.remove()
      }
      throw reason
    }
  }, [mvId, projectId])

  useEffect(() => {
    const host = hostRef.current
    if (!host || !available) return
    let cancelled = false
    const revision = ++reloadRef.current
    setStatus('loading')
    setError('')
    setPlaying(true)
    playingRef.current = true
    setState(DEFAULT_MV_STATE)
    host.replaceChildren()

    void mountDirectPlayer(DEFAULT_MV_STATE, revision, true)
      .then((mounted) => {
        if (!mounted || cancelled || revision !== reloadRef.current) return
        setStatus('direct')
      })
      .catch((reason: unknown) => {
        if (cancelled || revision !== reloadRef.current) return
        setError(reason instanceof Error ? reason.message : 'PM3 MV 预览加载失败')
        setStatus('error')
      })

    return () => {
      cancelled = true
      reloadRef.current += 1
      directLoadRef.current += 1
      apiRef.current?.suspend()
      apiRef.current = null
      elementRef.current = null
      host.replaceChildren()
    }
  }, [available, mountDirectPlayer, mvId, projectId, reloadRevision])

  const selectState = (next: number) => {
    if (!apiRef.current || !elementRef.current || status !== 'direct') return
    const revision = reloadRef.current
    void mountDirectPlayer(next, revision)
      .catch((reason: unknown) => {
        if (revision !== reloadRef.current) return
        setError(reason instanceof Error ? reason.message : 'PM3 MV 状态加载失败')
        setStatus('error')
      })
    setState(next)
  }

  const togglePlayback = () => {
    const player = apiRef.current
    if (!player || status === 'loading' || status === 'error') return
    if (playing) player.suspend()
    else player.resume()
    playingRef.current = !playing
    setPlaying(!playing)
  }

  return (
    <section className="pm3-mv-preview" aria-label={`MV ${mvId} 预览`}>
      <header>
        <div>
          <span><Radio size={12} />MV {mvId}</span>
          <strong>{status === 'direct'
              ? 'VISUAL'
              : status === 'error'
                ? 'ERROR'
                : 'LOADING'}</strong>
        </div>
        <div className="pm3-mv-player-actions">
          <button
            type="button"
            className="icon-button"
            onClick={togglePlayback}
            disabled={!available || status === 'loading' || status === 'error'}
            title={playing ? '暂停 MV' : '播放 MV'}
          >
            {playing ? <CirclePause size={15} /> : <CirclePlay size={15} />}
          </button>
          <button
            type="button"
            className="icon-button"
            onClick={() => setReloadRevision((value) => value + 1)}
            disabled={!available || status === 'loading'}
            title="重新加载 MV"
          >
            <RefreshCw size={15} />
          </button>
        </div>
      </header>

      <div className="pm3-mv-stage">
        <div className="pm3-mv-player-host" ref={hostRef} />
        {!available && <span>MV 资源不可用</span>}
        {status === 'loading' && available && <span>加载 Ruffle...</span>}
        {status === 'error' && <span>{error}</span>}
      </div>

      <footer>
        <div className="pm3-mv-state-control" role="group" aria-label="MV 状态">
          {MV_STATES.map((label, index) => (
            <button
              key={label}
              type="button"
              className={state === index ? 'active' : ''}
              aria-pressed={state === index}
              onClick={() => selectState(index)}
              disabled={status !== 'direct'}
            >
              {label}
            </button>
          ))}
        </div>
      </footer>
    </section>
  )
}
