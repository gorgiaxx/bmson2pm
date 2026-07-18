import {
  CirclePause,
  CirclePlay,
  RefreshCw,
  Radio,
  Zap,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
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

function controllerUrl(projectId: string): string {
  return `/api/projects/${encodeURIComponent(projectId)}/pm3/mv-preview/mvctrl/mvctrl.swf`
}

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

export function Pm3MvPreview({ projectId, mvId, available }: Pm3MvPreviewProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const elementRef = useRef<RufflePlayerElement | null>(null)
  const apiRef = useRef<RufflePlayerApi | null>(null)
  const reloadRef = useRef(0)
  const stateLoadRef = useRef(0)
  const [reloadRevision, setReloadRevision] = useState(0)
  const [status, setStatus] = useState<'loading' | 'controller' | 'direct' | 'error'>('loading')
  const [error, setError] = useState('')
  const [playing, setPlaying] = useState(true)
  const [state, setState] = useState(DEFAULT_MV_STATE)
  const [cont, setCont] = useState(false)

  useEffect(() => {
    const host = hostRef.current
    if (!host || !available) return
    let cancelled = false
    const revision = ++reloadRef.current
    setStatus('loading')
    setError('')
    setPlaying(true)
    setState(DEFAULT_MV_STATE)
    setCont(false)
    host.replaceChildren()

    void createPm3MvPlayer()
      .then(async (element) => {
        if (cancelled || revision !== reloadRef.current) return
        element.style.width = '100%'
        element.style.height = '100%'
        elementRef.current = element
        const player = element.ruffle()
        apiRef.current = player
        host.appendChild(element)

        await player.load(loadOptions(controllerUrl(projectId)))
        if (cancelled || revision !== reloadRef.current) return

        // Scaleform calls global AS2 functions directly. Ruffle can only drive them
        // when the SWF exposes an ExternalInterface callback, so retain a visual
        // fallback that opens the selected template without the controller.
        if (typeof element.MVLoad === 'function') {
          player.callExternalInterface('MVLoad', mvId)
          setStatus('controller')
        } else {
          await player.load(loadOptions(
            mvUrl(projectId, mvId, DEFAULT_MV_STATE, ++stateLoadRef.current),
          ))
          if (cancelled || revision !== reloadRef.current) return
          setStatus('direct')
        }
      })
      .catch((reason: unknown) => {
        if (cancelled || revision !== reloadRef.current) return
        setError(reason instanceof Error ? reason.message : 'PM3 MV 预览加载失败')
        setStatus('error')
      })

    return () => {
      cancelled = true
      reloadRef.current += 1
      apiRef.current?.suspend()
      apiRef.current = null
      elementRef.current = null
      host.replaceChildren()
    }
  }, [available, mvId, projectId, reloadRevision])

  const selectState = (next: number) => {
    const player = apiRef.current
    if (!player || (status !== 'controller' && status !== 'direct')) return
    if (status === 'controller') {
      player.callExternalInterface('MVState', next)
    } else {
      void player.load(loadOptions(mvUrl(projectId, mvId, next, ++stateLoadRef.current)))
        .then(() => {
          if (!playing) player.suspend()
        })
        .catch((reason: unknown) => {
          setError(reason instanceof Error ? reason.message : 'PM3 MV 状态加载失败')
          setStatus('error')
        })
    }
    setState(next)
  }

  const togglePlayback = () => {
    const player = apiRef.current
    if (!player || status === 'loading' || status === 'error') return
    if (playing) player.suspend()
    else player.resume()
    setPlaying((value) => !value)
  }

  const toggleCont = () => {
    if (status !== 'controller') return
    const next = !cont
    apiRef.current?.callExternalInterface('MVCont', next)
    setCont(next)
  }

  return (
    <section className="pm3-mv-preview" aria-label={`MV ${mvId} 预览`}>
      <header>
        <div>
          <span><Radio size={12} />MV {mvId}</span>
          <strong>{status === 'controller'
            ? 'CONTROL'
            : status === 'direct'
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
              disabled={status !== 'controller' && status !== 'direct'}
            >
              {label}
            </button>
          ))}
        </div>
        <button
          type="button"
          className={`icon-button ${cont ? 'active' : ''}`}
          aria-pressed={cont}
          onClick={toggleCont}
          disabled={status !== 'controller'}
          title="切换连续状态"
        >
          <Radio size={14} />
        </button>
        <button
          type="button"
          className="icon-button"
          onClick={() => apiRef.current?.callExternalInterface('MVHeavy')}
          disabled={status !== 'controller'}
          title="触发 Heavy"
        >
          <Zap size={14} />
        </button>
      </footer>
    </section>
  )
}
