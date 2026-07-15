import { Eye, EyeOff, Film, ImageOff, Layers3, Zap } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { activeBgaEvent, bgaResourceUrl, readBmsBga } from '../bga'
import { formatTime } from '../timing'
import type { BgaEvent, BgaResource } from '../bga'
import type { SongProject } from '../types'

interface BgaPreviewProps {
  project: SongProject
  position: number
  playing: boolean
  speed: number
}

interface BgaMediaProps {
  resource: BgaResource
  event: BgaEvent
  position: number
  playing: boolean
  speed: number
  layer?: boolean
  poor?: boolean
}

function BgaMedia({ resource, event, position, playing, speed, layer = false, poor = false }: BgaMediaProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const syncRef = useRef<() => void>(() => undefined)
  const url = bgaResourceUrl(resource)
  const syncVideo = useCallback(() => {
    const video = videoRef.current
    if (!video || !url || video.readyState < 1) return
    const desired = Math.max(0, position - event.time)
    const duration = Number.isFinite(video.duration) ? video.duration : 0
    const target = duration > 0 ? Math.min(desired, Math.max(0, duration - 0.001)) : desired
    const tolerance = playing ? 0.4 : 0.025
    if (Math.abs(video.currentTime - target) > tolerance) video.currentTime = target
    video.playbackRate = speed
    if (playing && (!duration || desired < duration)) {
      void video.play().catch(() => undefined)
    } else {
      video.pause()
    }
  }, [event.time, playing, position, speed, url])
  syncRef.current = syncVideo

  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    const handleMetadata = () => syncRef.current()
    video.addEventListener('loadedmetadata', handleMetadata)
    return () => video.removeEventListener('loadedmetadata', handleMetadata)
  }, [url])

  useEffect(() => syncVideo(), [syncVideo])

  const className = poor ? 'bga-poor-media' : layer ? 'bga-layer-media' : ''

  if (!url) return null
  if (resource.kind === 'image') {
    return <img className={className} src={url} alt="" draggable={false} />
  }
  if (resource.kind === 'video') {
    return (
      <video
        ref={videoRef}
        className={className}
        src={url}
        muted
        playsInline
        preload="auto"
      />
    )
  }
  return null
}

function eventLabel(event: BgaEvent | null, resource: BgaResource | undefined): string {
  if (!event) return '未触发'
  if (!resource) return `BMP ${event.bmpId} · 未定义`
  return resource.filename
}

export function BgaPreview({ project, position, playing, speed }: BgaPreviewProps) {
  const data = useMemo(() => readBmsBga(project), [project])
  const [visible, setVisible] = useState(true)
  const [poorVisible, setPoorVisible] = useState(false)
  useEffect(() => {
    setVisible(true)
    setPoorVisible(false)
  }, [project.id])

  const baseEvent = data ? activeBgaEvent(data.events.base, position) : null
  const layerEvent = data ? activeBgaEvent(data.events.layer, position) : null
  const poorEvent = data ? activeBgaEvent(data.events.poor, position) : null
  const baseResource = baseEvent ? data?.resources.get(baseEvent.bmpId) : undefined
  const layerResource = layerEvent ? data?.resources.get(layerEvent.bmpId) : undefined
  const poorResource = poorEvent ? data?.resources.get(poorEvent.bmpId) : undefined
  const totalEvents = data
    ? data.events.base.length + data.events.layer.length + data.events.poor.length
    : 0
  const availableResources = data
    ? [...data.resources.values()].filter((resource) => resource.exists).length
    : 0
  const activeResource = poorVisible && poorResource ? poorResource : baseResource
  const previewError = activeResource?.previewError || layerResource?.previewError || ''

  if (!data || (!data.resources.size && totalEvents === 0)) {
    return (
      <div className="inspector-body bga-inspector-body">
        <div className="inspector-heading"><div><span>BGA 预览</span><strong>当前项目没有视觉事件</strong></div></div>
        <div className="empty-state bga-empty"><Film size={30} /><strong>没有 BGA</strong></div>
      </div>
    )
  }

  const renderMedia = (
    event: BgaEvent | null,
    resource: BgaResource | undefined,
    mode: 'base' | 'layer' | 'poor' = 'base',
  ) => event && resource && resource.exists && resource.kind !== 'unsupported'
    ? (
        <BgaMedia
          resource={resource}
          event={event}
          position={position}
          playing={playing}
          speed={speed}
          layer={mode === 'layer'}
          poor={mode === 'poor'}
        />
      )
    : null

  return (
    <div className="inspector-body bga-inspector-body">
      <div className="inspector-heading bga-heading">
        <div><span>BGA 预览</span><strong>{totalEvents} 个事件 · {availableResources}/{data.resources.size} 资源</strong></div>
        <button type="button" className={`icon-button ${visible ? 'active' : ''}`} onClick={() => setVisible((value) => !value)} title={visible ? '隐藏 BGA' : '显示 BGA'}>
          {visible ? <Eye size={15} /> : <EyeOff size={15} />}
        </button>
      </div>

      <div className={`bga-stage ${visible ? '' : 'hidden'}`} aria-label="BGA 画面">
        {visible && (
          <>
            {renderMedia(baseEvent, baseResource)}
            {renderMedia(layerEvent, layerResource, 'layer')}
            {poorVisible && renderMedia(poorEvent, poorResource, 'poor')}
          </>
        )}
        {(!visible || (!baseResource?.exists && !(poorVisible && poorResource?.exists))) && (
          <div className="bga-stage-placeholder">
            {visible ? <ImageOff size={24} /> : <EyeOff size={24} />}
            <span>{visible ? '当前位置没有可预览的 Base BGA' : 'BGA 已隐藏'}</span>
          </div>
        )}
        <span className="bga-timecode">{formatTime(position)}</span>
        {poorVisible && <span className="bga-poor-badge">POOR</span>}
      </div>

      <div className="bga-controls">
        <button
          type="button"
          className={`button secondary ${poorVisible ? 'active' : ''}`}
          onClick={() => setPoorVisible((value) => !value)}
          disabled={!poorEvent || !poorResource?.exists}
        >
          <Zap size={14} />{poorVisible ? '结束 Poor' : '模拟 Poor'}
        </button>
        <span>{speed.toFixed(2)}×</span>
      </div>

      {previewError && <div className="bga-preview-warning">预览代理生成失败：{previewError}</div>}

      <div className="bga-event-summary">
        <div><span className="base"><Film size={13} />Base</span><strong>{eventLabel(baseEvent, baseResource)}</strong><small>{baseEvent ? `BMP ${baseEvent.bmpId} · ${formatTime(baseEvent.time)}` : `${data.events.base.length} 事件`}</small></div>
        <div><span className="layer"><Layers3 size={13} />Layer</span><strong>{eventLabel(layerEvent, layerResource)}</strong><small>{layerEvent ? `BMP ${layerEvent.bmpId} · ${formatTime(layerEvent.time)}` : `${data.events.layer.length} 事件`}</small></div>
        <div><span className="poor"><Zap size={13} />Poor</span><strong>{eventLabel(poorEvent, poorResource)}</strong><small>{poorEvent ? `BMP ${poorEvent.bmpId} · ${formatTime(poorEvent.time)}` : `${data.events.poor.length} 事件`}</small></div>
      </div>
    </div>
  )
}
