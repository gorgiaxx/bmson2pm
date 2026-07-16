import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { buildAudioPeaks, DecodedAudioBufferCache } from '../audio'
import type { DifficultyId, KeySoundAsset, Note, SongProject } from '../types'
import { createTimingIndex, pulseToSeconds } from '../timing'

const LOOKAHEAD_SECONDS = 0.18
const SCHEDULER_INTERVAL_MS = 25
const DECODED_AUDIO_CACHE_BYTES = 256 * 1024 * 1024
const PRELOAD_WORKERS = 8
const CONTEXT_RESUME_WAIT_MS = 80
const PM3_SOURCE_PPQN = 12
const MIN_PRIMARY_BGM_SECONDS = 8
const DEFAULT_ROLL_DIVISOR = 4
const ROLL_PREVIEW_RATE = 1.5
const MAX_ROLL_HITS_PER_NOTE = 2048
const LANE_FREQUENCIES = [190, 230, 310, 370, 130, 155]

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

export interface BmsBgmScheduleItem {
  key: string
  assetId: string
  pulse: number
  time: number
  value: string
  line: number
}

export interface AutomaticMusic {
  assetId: string
  eventKey: string
  name: string
  buffer: AudioBuffer
  peaks: Float32Array
  startTime: number
  volume: number
}

export interface MediaPlaybackWindow {
  delay: number
  offset: number
}

export interface ProjectAudioResource {
  assetId: string
  eventKey: string
  name: string
  url: string
  startTime: number
  volume: number
}

export interface NotePlaybackScheduleItem {
  key: string
  note: Note
  pulse: number
  time: number
  hitIndex: number
  hitCount: number
}

function pm3RollHitCount(note: Note): number | null {
  const pm3 = record(note.extensions?.pm3)
  const event = record(pm3?.event)
  const value = Number(event?.hold_number)
  return Number.isInteger(value) && value > 0 ? value : null
}

export function rollHitPulses(note: Note, resolution: number): number[] {
  if (note.length <= 0) return [note.pulse]
  const fallbackStep = Math.max(1, Math.round(resolution / DEFAULT_ROLL_DIVISOR))
  const baseHitCount = pm3RollHitCount(note)
    ?? Math.max(2, Math.ceil(note.length / fallbackStep) + 1)
  const requestedCount = Math.round((baseHitCount - 1) * ROLL_PREVIEW_RATE) + 1
  const hitCount = Math.max(2, Math.min(
    requestedCount,
    MAX_ROLL_HITS_PER_NOTE,
  ))
  return Array.from({ length: hitCount }, (_, index) => (
    note.pulse + index * note.length / (hitCount - 1)
  ))
}

export function createNotePlaybackSchedule(
  project: SongProject,
  difficulty: DifficultyId,
): NotePlaybackScheduleItem[] {
  const timingIndex = createTimingIndex(project.timing)
  const offset = project.timing.key_sound_offset_ms / 1000
  return project.difficulties[difficulty].notes.flatMap((note) => {
    const pulses = rollHitPulses(note, project.timing.resolution)
    return pulses.map((pulse, hitIndex) => ({
      key: pulses.length === 1 ? `note:${note.id}` : `note:${note.id}:roll:${hitIndex}`,
      note,
      pulse,
      time: pulseToSeconds(project, pulse, timingIndex) + offset,
      hitIndex,
      hitCount: pulses.length,
    }))
  }).sort((left, right) => (
    left.time - right.time
    || left.note.pulse - right.note.pulse
    || left.hitIndex - right.hitIndex
    || left.note.id.localeCompare(right.note.id)
  ))
}

export function createBmsBgmSchedule(project: SongProject): BmsBgmScheduleItem[] {
  const raw = project.unknown_data.bms_bgm_objects
  if (!Array.isArray(raw)) return []
  const timingIndex = createTimingIndex(project.timing)
  return raw.flatMap((value, index) => {
    const item = record(value)
    const assetId = typeof item?.key_sound_id === 'string' ? item.key_sound_id : ''
    const pulse = Number(item?.pulse)
    if (!assetId || !Number.isFinite(pulse) || pulse < 0) return []
    const line = Number.isFinite(Number(item?.line)) ? Number(item?.line) : 0
    const objectValue = typeof item?.value === 'string' ? item.value : ''
    const position = typeof item?.position === 'string' ? item.position : ''
    return [{
      key: `bms-bgm:${line}:${position}:${objectValue}:${index}`,
      assetId,
      pulse,
      time: pulseToSeconds(project, pulse, timingIndex),
      value: objectValue,
      line,
    }]
  }).sort((left, right) => left.time - right.time || left.line - right.line || left.key.localeCompare(right.key))
}

export function selectBmsPrimaryEvent(
  schedule: BmsBgmScheduleItem[],
  buffers: ReadonlyMap<string, AudioBuffer>,
): BmsBgmScheduleItem | null {
  const firstByAsset = new Map<string, BmsBgmScheduleItem>()
  for (const event of schedule) {
    if (!firstByAsset.has(event.assetId)) firstByAsset.set(event.assetId, event)
  }
  let selected: BmsBgmScheduleItem | null = null
  let selectedDuration = MIN_PRIMARY_BGM_SECONDS
  for (const [assetId, event] of firstByAsset) {
    const duration = buffers.get(assetId)?.duration ?? 0
    if (duration < selectedDuration) continue
    if (duration === selectedDuration && selected && event.time >= selected.time) continue
    selected = event
    selectedDuration = duration
  }
  return selected
}

export function mediaPlaybackWindow(
  position: number,
  mediaStart: number,
  mediaDuration: number,
): MediaPlaybackWindow | null {
  const mediaEnd = mediaStart + mediaDuration
  if (mediaDuration <= 0 || position >= mediaEnd - 0.000001) return null
  return {
    delay: Math.max(0, mediaStart - position),
    offset: Math.max(0, position - mediaStart),
  }
}

export function keySoundResourceUrl(asset: KeySoundAsset): string | null {
  const editor = record(asset.extensions?.editor)
  const editorResource = record(editor?.resource)
  const bms = record(asset.extensions?.bms)
  const bmsResource = record(bms?.resource)
  const projectResource = editorResource && editorResource.exists !== false ? editorResource : bmsResource
  if (projectResource && projectResource.exists !== false) {
    const projectId = typeof projectResource.project_id === 'string' ? projectResource.project_id : ''
    const rawPath = typeof projectResource.path === 'string' ? projectResource.path : ''
    const path = rawPath.replaceAll('\\', '/')
    if (projectId && path && !path.split('/').includes('..')) {
      const query = new URLSearchParams({ path })
      return `/api/projects/${encodeURIComponent(projectId)}/key-sound?${query}`
    }
  }
  const pm3 = record(asset.extensions?.pm3)
  const resource = record(pm3?.resource)
  if (!resource || resource.exists === false) return null
  const root = typeof resource.root_id === 'string' ? resource.root_id : ''
  const rawPath = typeof resource.path === 'string' ? resource.path : ''
  const path = rawPath.replaceAll('\\', '/')
  if (root !== 'game' || !path.toLowerCase().startsWith('media/sound/note/')) return null
  if (path.split('/').includes('..')) return null
  const query = new URLSearchParams({ root, path })
  return `/api/pm3/key-sound?${query}`
}

export function prioritizeKeySoundAssets(
  project: SongProject,
  difficulty: DifficultyId,
): KeySoundAsset[] {
  const assetsById = new Map(project.key_sounds.map((asset) => [asset.id, asset]))
  const ordered: KeySoundAsset[] = []
  const included = new Set<string>()
  const append = (assetId: string | null | undefined) => {
    if (!assetId || included.has(assetId)) return
    const asset = assetsById.get(assetId)
    if (!asset) return
    included.add(assetId)
    ordered.push(asset)
  }
  for (const event of createBmsBgmSchedule(project)) append(event.assetId)
  for (const note of [...project.difficulties[difficulty].notes].sort((left, right) => left.pulse - right.pulse)) {
    append(note.key_sound_id)
  }
  for (const lane of project.lanes) append(lane.default_key_sound_id)
  for (const asset of project.key_sounds) append(asset.id)
  return ordered
}

export function pm3BackgroundAudioResource(
  project: SongProject,
  difficulty: DifficultyId,
): ProjectAudioResource | null {
  if (project.metadata.import_format !== 'pm3') return null
  const asset = project.audio_assets.find((item) => (
    typeof item.filename === 'string'
    && item.filename.replaceAll('\\', '/').toLowerCase().startsWith('media/sound/bg/')
  ))
  if (!asset) return null
  const path = asset.filename.replaceAll('\\', '/')
  if (path.split('/').includes('..')) return null
  const source = project.source_files.find((item) => {
    const value = record(item)
    return value?.role === 'background-audio' && value.path === asset.filename
  })
  const sourceRecord = record(source)
  if (sourceRecord?.exists === false || (sourceRecord?.root_id && sourceRecord.root_id !== 'game')) return null

  const chartPm3 = record(project.difficulties[difficulty].extensions?.pm3)
  const rawEvents = Array.isArray(chartPm3?.background_events) ? chartPm3.background_events : []
  const events = rawEvents
    .map(record)
    .filter((event): event is Record<string, unknown> => Boolean(event))
    .map((event) => ({ tick: Number(event.tick), volume: Number(event.volume) }))
    .filter((event) => Number.isFinite(event.tick) && event.tick >= 0)
    .sort((left, right) => left.tick - right.tick)
  const first = events[0]
  const pulse = first
    ? Math.round(first.tick * project.timing.resolution / PM3_SOURCE_PPQN)
    : 0
  const query = new URLSearchParams({ root: 'game', path })
  return {
    assetId: asset.id,
    eventKey: `pm3-background:${difficulty}:${first?.tick ?? 0}:${path}`,
    name: asset.name || path.split('/').at(-1) || 'PM3 Background Music',
    url: `/api/pm3/audio?${query}`,
    startTime: pulseToSeconds(project, pulse, createTimingIndex(project.timing)),
    volume: first && Number.isFinite(first.volume)
      ? Math.max(0, Math.min(2, first.volume / 127))
      : 1,
  }
}

export function usePlayback(
  project: SongProject,
  difficulty: DifficultyId,
  manualAudioBuffer: AudioBuffer | null,
) {
  const [playing, setPlaying] = useState(false)
  const [position, setPosition] = useState(0)
  const [speed, setSpeedState] = useState(1)
  const [loop, setLoop] = useState(false)
  const [musicMuted, setMusicMutedState] = useState(false)
  const [keySoundStatus, setKeySoundStatus] = useState({ ready: 0, total: 0, failed: 0 })
  const [autoMusic, setAutoMusic] = useState<AutomaticMusic | null>(null)
  const [autoMusicLoading, setAutoMusicLoading] = useState<string | null>(null)
  const contextRef = useRef<AudioContext | null>(null)
  const musicBusRef = useRef<GainNode | null>(null)
  const startClockRef = useRef(0)
  const startPositionRef = useRef(0)
  const positionRef = useRef(0)
  const speedRef = useRef(1)
  const playingRef = useRef(false)
  const musicMutedRef = useRef(false)
  const scrubWasPlayingRef = useRef(false)
  const sourceRef = useRef<AudioBufferSourceNode | null>(null)
  const keySoundBuffersRef = useRef(new Map<string, AudioBuffer>())
  const keySoundBufferUrlsRef = useRef(new Map<string, string>())
  const keySoundFailuresRef = useRef(new Set<string>())
  const decodedResourceCacheRef = useRef(new DecodedAudioBufferCache(DECODED_AUDIO_CACHE_BYTES))
  const resourceLoadsRef = useRef(new Map<string, Promise<AudioBuffer>>())
  const warmedContextRef = useRef<AudioContext | null>(null)
  const keySoundSourcesRef = useRef(new Set<AudioBufferSourceNode>())
  const bgmSourcesRef = useRef(new Set<AudioBufferSourceNode>())
  const scheduledRef = useRef(new Set<string>())
  const frameRef = useRef(0)
  const timerRef = useRef(0)
  const assetsById = useMemo(
    () => new Map(project.key_sounds.map((asset) => [asset.id, asset])),
    [project.key_sounds],
  )
  const bmsBgmSchedule = useMemo(
    () => createBmsBgmSchedule(project),
    [project.id, project.timing, project.unknown_data.bms_bgm_objects],
  )
  const prioritizedAssets = useMemo(
    () => prioritizeKeySoundAssets(project, difficulty),
    [difficulty, project.id, project.key_sounds],
  )
  const pm3MusicResource = useMemo(
    () => pm3BackgroundAudioResource(project, difficulty),
    [
      difficulty,
      project.id,
      project.audio_assets,
      project.difficulties[difficulty].extensions,
      project.source_files,
      project.timing,
    ],
  )
  const laneKeySounds = useMemo(() => {
    const result = new Map<number, string>()
    for (const lane of project.lanes) {
      if (lane.default_key_sound_id) result.set(lane.id, lane.default_key_sound_id)
    }
    for (const asset of project.key_sounds) {
      for (const laneId of asset.lane_ids) {
        if (!result.has(laneId)) result.set(laneId, asset.id)
      }
    }
    return result
  }, [project.key_sounds, project.lanes])
  const noteSchedule = useMemo(
    () => createNotePlaybackSchedule(project, difficulty),
    [difficulty, project],
  )

  const context = useCallback((): AudioContext | null => {
    if (contextRef.current?.state === 'closed') {
      contextRef.current = null
      musicBusRef.current = null
    }
    if (!contextRef.current && typeof AudioContext !== 'undefined') {
      contextRef.current = new AudioContext({ latencyHint: 'interactive' })
    }
    return contextRef.current
  }, [])

  const musicBus = useCallback((): GainNode | null => {
    const ctx = context()
    if (!ctx) return null
    if (!musicBusRef.current) {
      const gain = ctx.createGain()
      gain.gain.value = musicMutedRef.current ? 0 : 1
      gain.connect(ctx.destination)
      musicBusRef.current = gain
    }
    return musicBusRef.current
  }, [context])

  const loadDecodedResource = useCallback(async (url: string): Promise<AudioBuffer> => {
    const cached = decodedResourceCacheRef.current.get(url)
    if (cached) return cached
    const existing = resourceLoadsRef.current.get(url)
    if (existing) return existing
    const ctx = context()
    if (!ctx) throw new Error('浏览器不支持 Web Audio')
    const pending = (async () => {
      const response = await fetch(url)
      if (!response.ok) throw new Error(`Audio request failed (${response.status})`)
      const payload = await response.arrayBuffer()
      const buffer = await ctx.decodeAudioData(payload)
      decodedResourceCacheRef.current.set(url, buffer)
      return buffer
    })()
    resourceLoadsRef.current.set(url, pending)
    try {
      return await pending
    } finally {
      if (resourceLoadsRef.current.get(url) === pending) resourceLoadsRef.current.delete(url)
    }
  }, [context])

  const resumeContext = useCallback(async (): Promise<AudioContext | null> => {
    const ctx = context()
    if (!ctx) return null
    if (ctx.state === 'suspended') {
      try {
        await Promise.race([
          ctx.resume(),
          new Promise<void>((resolve) => window.setTimeout(resolve, CONTEXT_RESUME_WAIT_MS)),
        ])
      } catch {
        // A later trusted input can retry; visual playback should not deadlock.
      }
    }
    if (ctx.state === 'closed') return null
    if (ctx.state === 'running' && warmedContextRef.current !== ctx) {
      const source = ctx.createBufferSource()
      source.buffer = ctx.createBuffer(1, 1, ctx.sampleRate)
      source.connect(ctx.destination)
      source.start()
      warmedContextRef.current = ctx
    }
    return ctx
  }, [context])

  useEffect(() => {
    const unlock = () => { void resumeContext() }
    window.addEventListener('pointerdown', unlock, { capture: true, once: true })
    window.addEventListener('keydown', unlock, { capture: true, once: true })
    return () => {
      window.removeEventListener('pointerdown', unlock, { capture: true })
      window.removeEventListener('keydown', unlock, { capture: true })
    }
  }, [resumeContext])

  useEffect(() => {
    let cancelled = false
    const available = prioritizedAssets
      .map((asset) => ({ asset, url: keySoundResourceUrl(asset) }))
      .filter((item): item is { asset: KeySoundAsset; url: string } => Boolean(item.url))
    const validIds = new Set(project.key_sounds.map((asset) => asset.id))
    for (const id of keySoundBuffersRef.current.keys()) {
      if (!validIds.has(id)) {
        keySoundBuffersRef.current.delete(id)
        keySoundBufferUrlsRef.current.delete(id)
      }
    }
    for (const { asset, url } of available) {
      if (keySoundBufferUrlsRef.current.get(asset.id) !== url) {
        keySoundBuffersRef.current.delete(asset.id)
        keySoundBufferUrlsRef.current.delete(asset.id)
      }
      const cached = decodedResourceCacheRef.current.get(url)
      if (cached) {
        keySoundBuffersRef.current.set(asset.id, cached)
        keySoundBufferUrlsRef.current.set(asset.id, url)
      }
    }
    keySoundFailuresRef.current.clear()
    const queue = available.filter(({ asset }) => !keySoundBuffersRef.current.has(asset.id))
    let ready = available.length - queue.length
    let failed = 0
    let statusFrame = 0
    const publishStatus = () => {
      if (statusFrame) return
      statusFrame = window.requestAnimationFrame(() => {
        statusFrame = 0
        setKeySoundStatus((current) => (
          current.ready === ready && current.total === available.length && current.failed === failed
            ? current
            : { ready, total: available.length, failed }
        ))
      })
    }
    publishStatus()
    let cursor = 0

    const worker = async () => {
      while (!cancelled) {
        const item = queue[cursor]
        cursor += 1
        if (!item) return
        try {
          const buffer = await loadDecodedResource(item.url)
          if (!cancelled) {
            keySoundBuffersRef.current.set(item.asset.id, buffer)
            keySoundBufferUrlsRef.current.set(item.asset.id, item.url)
            ready += 1
            publishStatus()
          }
        } catch {
          if (!cancelled) {
            keySoundFailuresRef.current.add(item.asset.id)
            failed += 1
            publishStatus()
          }
        }
      }
    }
    const count = Math.min(PRELOAD_WORKERS, queue.length)
    void Promise.all(Array.from({ length: count }, worker))
    return () => {
      cancelled = true
      window.cancelAnimationFrame(statusFrame)
    }
  }, [loadDecodedResource, prioritizedAssets, project.key_sounds])

  useEffect(() => {
    let cancelled = false
    if (pm3MusicResource) {
      setAutoMusicLoading(pm3MusicResource.name)
      void loadDecodedResource(pm3MusicResource.url)
        .then((buffer) => {
          if (cancelled) return
          setAutoMusic({
            assetId: pm3MusicResource.assetId,
            eventKey: pm3MusicResource.eventKey,
            name: pm3MusicResource.name,
            buffer,
            peaks: buildAudioPeaks(buffer),
            startTime: pm3MusicResource.startTime,
            volume: pm3MusicResource.volume,
          })
          setAutoMusicLoading(null)
        })
        .catch(() => {
          if (cancelled) return
          setAutoMusic(null)
          setAutoMusicLoading(null)
        })
      return () => { cancelled = true }
    }
    const event = selectBmsPrimaryEvent(bmsBgmSchedule, keySoundBuffersRef.current)
    const buffer = event ? keySoundBuffersRef.current.get(event.assetId) : null
    const asset = event ? assetsById.get(event.assetId) : null
    if (!event || !buffer || !asset) {
      setAutoMusic(null)
      setAutoMusicLoading(bmsBgmSchedule.length ? 'BMS 自动音频' : null)
      return
    }
    const name = asset.name || asset.filename || `BMS WAV ${event.value}`
    const startTime = event.time + asset.delay_ms / 1000
    setAutoMusic((current) => {
      if (
        current?.eventKey === event.key
        && current.buffer === buffer
        && current.name === name
        && current.startTime === startTime
        && current.volume === asset.volume
      ) return current
      return {
        assetId: event.assetId,
        eventKey: event.key,
        name,
        buffer,
        peaks: buildAudioPeaks(buffer),
        startTime,
        volume: asset.volume,
      }
    })
    setAutoMusicLoading(null)
    return () => { cancelled = true }
  }, [assetsById, bmsBgmSchedule, keySoundStatus.ready, loadDecodedResource, pm3MusicResource])

  const effectiveMusic = useMemo(() => {
    const offset = project.timing.audio_offset_ms / 1000
    if (manualAudioBuffer) return { buffer: manualAudioBuffer, startTime: offset, volume: 1 }
    if (autoMusic) {
      return {
        buffer: autoMusic.buffer,
        startTime: autoMusic.startTime + offset,
        volume: autoMusic.volume,
      }
    }
    return null
  }, [autoMusic, manualAudioBuffer, project.timing.audio_offset_ms])
  const duration = Math.max(
    project.metadata.audio_duration,
    effectiveMusic ? effectiveMusic.startTime + effectiveMusic.buffer.duration : 0,
  )

  const clockTime = useCallback(() => (
    typeof performance !== 'undefined' ? performance.now() / 1000 : Date.now() / 1000
  ), [])

  const triggerFallback = useCallback((laneId: number, at?: number, volume = 0.55) => {
    const ctx = context()
    if (!ctx) return
    if (ctx.state === 'suspended') void resumeContext()
    const start = Math.max(ctx.currentTime, at ?? ctx.currentTime)
    const oscillator = ctx.createOscillator()
    const gain = ctx.createGain()
    oscillator.type = laneId <= 2 ? 'triangle' : laneId <= 4 ? 'square' : 'sine'
    oscillator.frequency.setValueAtTime(LANE_FREQUENCIES[laneId - 1] ?? 220, start)
    oscillator.frequency.exponentialRampToValueAtTime(70, start + 0.07)
    gain.gain.setValueAtTime(Math.max(0.001, volume), start)
    gain.gain.exponentialRampToValueAtTime(0.001, start + 0.09)
    oscillator.connect(gain).connect(ctx.destination)
    oscillator.start(start)
    oscillator.stop(start + 0.1)
  }, [context, resumeContext])

  const triggerKeySound = useCallback((assetId: string, at?: number, volume = 0.72) => {
    const buffer = keySoundBuffersRef.current.get(assetId)
    const asset = assetsById.get(assetId)
    if (!buffer || !asset) return false
    const ctx = context()
    if (!ctx) return false
    if (ctx.state === 'suspended') void resumeContext()
    const source = ctx.createBufferSource()
    const gain = ctx.createGain()
    const delay = asset.delay_ms / 1000 / speedRef.current
    const start = Math.max(ctx.currentTime, (at ?? ctx.currentTime) + delay)
    source.buffer = buffer
    source.playbackRate.value = speedRef.current
    gain.gain.value = Math.max(0, Math.min(2, volume * asset.volume))
    source.connect(gain).connect(ctx.destination)
    keySoundSourcesRef.current.add(source)
    source.onended = () => keySoundSourcesRef.current.delete(source)
    source.start(start)
    return true
  }, [assetsById, context, resumeContext])

  const triggerBgm = useCallback((assetId: string, at: number, offset: number) => {
    const buffer = keySoundBuffersRef.current.get(assetId)
    const asset = assetsById.get(assetId)
    const ctx = context()
    const bus = musicBus()
    if (!buffer || !asset || !ctx || !bus || offset >= buffer.duration) return false
    const source = ctx.createBufferSource()
    const gain = ctx.createGain()
    source.buffer = buffer
    source.playbackRate.value = speedRef.current
    gain.gain.value = Math.max(0, Math.min(2, 0.82 * asset.volume))
    source.connect(gain).connect(bus)
    bgmSourcesRef.current.add(source)
    source.onended = () => bgmSourcesRef.current.delete(source)
    source.start(Math.max(ctx.currentTime, at), Math.max(0, offset))
    return true
  }, [assetsById, context, musicBus])

  const triggerLane = useCallback((laneId: number, at?: number, volume = 0.72) => {
    const assetId = laneKeySounds.get(laneId)
    if (assetId && triggerKeySound(assetId, at, volume)) return
    triggerFallback(laneId, at, volume * 0.75)
  }, [laneKeySounds, triggerFallback, triggerKeySound])

  const triggerNote = useCallback((note: Note, at?: number, volume = note.volume * 0.72) => {
    const assetId = note.key_sound_id ?? laneKeySounds.get(note.lane_id)
    if (assetId && triggerKeySound(assetId, at, volume)) return
    triggerFallback(note.lane_id, at, volume * 0.75)
  }, [laneKeySounds, triggerFallback, triggerKeySound])

  const stopSource = useCallback(() => {
    try { sourceRef.current?.stop() } catch { /* already stopped */ }
    sourceRef.current = null
  }, [])

  const stopKeySounds = useCallback(() => {
    for (const source of keySoundSourcesRef.current) {
      try { source.stop() } catch { /* already stopped */ }
    }
    keySoundSourcesRef.current.clear()
  }, [])

  const stopBgmSounds = useCallback(() => {
    for (const source of bgmSourcesRef.current) {
      try { source.stop() } catch { /* already stopped */ }
    }
    bgmSourcesRef.current.clear()
  }, [])

  const startMusic = useCallback((timelinePosition: number) => {
    if (!effectiveMusic) return
    const playbackWindow = mediaPlaybackWindow(
      timelinePosition,
      effectiveMusic.startTime,
      effectiveMusic.buffer.duration,
    )
    if (!playbackWindow) return
    const ctx = context()
    const bus = musicBus()
    if (!ctx || !bus) return
    const source = ctx.createBufferSource()
    const gain = ctx.createGain()
    source.buffer = effectiveMusic.buffer
    source.playbackRate.value = speedRef.current
    gain.gain.value = Math.max(0, Math.min(2, 0.82 * effectiveMusic.volume))
    source.connect(gain).connect(bus)
    source.start(
      ctx.currentTime + playbackWindow.delay / speedRef.current,
      playbackWindow.offset,
    )
    sourceRef.current = source
    source.onended = () => {
      if (sourceRef.current === source) sourceRef.current = null
    }
  }, [context, effectiveMusic, musicBus])

  const setMusicMuted = useCallback((muted: boolean) => {
    musicMutedRef.current = muted
    setMusicMutedState(muted)
    const ctx = contextRef.current
    const gain = musicBusRef.current
    if (!ctx || !gain) return
    gain.gain.cancelScheduledValues(ctx.currentTime)
    gain.gain.setTargetAtTime(muted ? 0 : 1, ctx.currentTime, 0.012)
  }, [])

  const currentPosition = useCallback(() => {
    if (!playingRef.current) return positionRef.current
    return startPositionRef.current + (clockTime() - startClockRef.current) * speedRef.current
  }, [clockTime])

  const schedule = useCallback(() => {
    if (!playingRef.current) return
    const ctx = context()
    if (!ctx) return
    const nowPosition = currentPosition()
    const horizon = nowPosition + LOOKAHEAD_SECONDS * speedRef.current
    const laneMute = new Map(project.lanes.map((lane) => [lane.id, lane.muted]))
    let low = 0
    let high = noteSchedule.length
    const earliest = nowPosition - 0.02
    while (low < high) {
      const middle = Math.floor((low + high) / 2)
      if (noteSchedule[middle].time < earliest) low = middle + 1
      else high = middle
    }
    for (let index = low; index < noteSchedule.length; index += 1) {
      const { key, note, time: noteTime } = noteSchedule[index]
      if (noteTime > horizon) break
      if (scheduledRef.current.has(key) || laneMute.get(note.lane_id)) continue
      const audioTime = ctx.currentTime + Math.max(0, (noteTime - nowPosition) / speedRef.current)
      triggerNote(note, audioTime)
      scheduledRef.current.add(key)
    }

    if (manualAudioBuffer) return
    for (const event of bmsBgmSchedule) {
      if (autoMusic?.eventKey === event.key || scheduledRef.current.has(event.key)) continue
      const buffer = keySoundBuffersRef.current.get(event.assetId)
      const asset = assetsById.get(event.assetId)
      if (!buffer || !asset) continue
      const eventTime = event.time
        + project.timing.audio_offset_ms / 1000
        + asset.delay_ms / 1000
      if (eventTime > horizon) continue
      const eventEnd = eventTime + buffer.duration
      if (eventEnd <= earliest) {
        scheduledRef.current.add(event.key)
        continue
      }
      const offset = Math.max(0, nowPosition - eventTime)
      const audioTime = ctx.currentTime + Math.max(0, (eventTime - nowPosition) / speedRef.current)
      if (triggerBgm(event.assetId, audioTime, offset)) scheduledRef.current.add(event.key)
    }
  }, [assetsById, autoMusic?.eventKey, bmsBgmSchedule, context, currentPosition, manualAudioBuffer, noteSchedule, project.lanes, project.timing.audio_offset_ms, triggerBgm, triggerNote])

  useEffect(() => {
    if (!playingRef.current) return
    const current = currentPosition()
    stopSource()
    stopBgmSounds()
    for (const key of [...scheduledRef.current]) {
      if (key.startsWith('bms-bgm:')) scheduledRef.current.delete(key)
    }
    startMusic(current)
    schedule()
  }, [autoMusic?.eventKey, currentPosition, manualAudioBuffer, schedule, startMusic, stopBgmSounds, stopSource])

  const stop = useCallback(() => {
    playingRef.current = false
    scrubWasPlayingRef.current = false
    setPlaying(false)
    window.cancelAnimationFrame(frameRef.current)
    window.clearInterval(timerRef.current)
    stopSource()
    stopKeySounds()
    stopBgmSounds()
    positionRef.current = 0
    setPosition(0)
    scheduledRef.current.clear()
  }, [stopBgmSounds, stopKeySounds, stopSource])

  const pause = useCallback(() => {
    const current = currentPosition()
    playingRef.current = false
    setPlaying(false)
    window.cancelAnimationFrame(frameRef.current)
    window.clearInterval(timerRef.current)
    stopSource()
    stopKeySounds()
    stopBgmSounds()
    positionRef.current = current
    setPosition(current)
  }, [currentPosition, stopBgmSounds, stopKeySounds, stopSource])

  const play = useCallback(async () => {
    if (playingRef.current) return
    const ctx = await resumeContext()
    if (!ctx || playingRef.current) return
    if (positionRef.current >= duration) positionRef.current = 0
    startPositionRef.current = positionRef.current
    startClockRef.current = clockTime()
    scheduledRef.current.clear()
    playingRef.current = true
    setPlaying(true)
    startMusic(positionRef.current)
    const tick = () => {
      if (!playingRef.current) return
      let current = currentPosition()
      if (current >= duration) {
        if (loop) {
          positionRef.current = 0
          startPositionRef.current = 0
          startClockRef.current = clockTime()
          scheduledRef.current.clear()
          stopSource()
          stopKeySounds()
          stopBgmSounds()
          startMusic(0)
          schedule()
          current = 0
        } else {
          stop()
          return
        }
      }
      positionRef.current = current
      setPosition(current)
      frameRef.current = window.requestAnimationFrame(tick)
    }
    frameRef.current = window.requestAnimationFrame(tick)
    schedule()
    timerRef.current = window.setInterval(schedule, SCHEDULER_INTERVAL_MS)
  }, [clockTime, currentPosition, duration, loop, resumeContext, schedule, startMusic, stop, stopBgmSounds, stopKeySounds, stopSource])

  const clampPosition = useCallback((seconds: number) => (
    Math.max(0, Math.min(seconds, duration))
  ), [duration])

  const seek = useCallback((seconds: number) => {
    const wasPlaying = playingRef.current
    if (wasPlaying) pause()
    positionRef.current = clampPosition(seconds)
    setPosition(positionRef.current)
    scheduledRef.current.clear()
    if (wasPlaying) window.setTimeout(() => { void play() }, 0)
  }, [clampPosition, pause, play])

  const beginScrub = useCallback(() => {
    scrubWasPlayingRef.current = playingRef.current
    if (playingRef.current) pause()
  }, [pause])

  const scrubTo = useCallback((seconds: number) => {
    positionRef.current = clampPosition(seconds)
    setPosition(positionRef.current)
    scheduledRef.current.clear()
  }, [clampPosition])

  const endScrub = useCallback(() => {
    const resume = scrubWasPlayingRef.current
    scrubWasPlayingRef.current = false
    if (resume) window.setTimeout(() => { void play() }, 0)
  }, [play])

  const setSpeed = useCallback((value: number) => {
    const next = Math.max(0.5, Math.min(1.5, value))
    const current = currentPosition()
    startPositionRef.current = current
    positionRef.current = current
    startClockRef.current = clockTime()
    speedRef.current = next
    setSpeedState(next)
    if (!playingRef.current) return
    stopSource()
    stopKeySounds()
    stopBgmSounds()
    scheduledRef.current.clear()
    startMusic(current)
    schedule()
  }, [clockTime, currentPosition, schedule, startMusic, stopBgmSounds, stopKeySounds, stopSource])

  useEffect(() => () => {
    window.cancelAnimationFrame(frameRef.current)
    window.clearInterval(timerRef.current)
    stopSource()
    stopKeySounds()
    stopBgmSounds()
    musicBusRef.current?.disconnect()
    musicBusRef.current = null
    keySoundBuffersRef.current.clear()
    keySoundBufferUrlsRef.current.clear()
    decodedResourceCacheRef.current.clear()
    resourceLoadsRef.current.clear()
    warmedContextRef.current = null
    const activeContext = contextRef.current
    contextRef.current = null
    if (activeContext && activeContext.state !== 'closed') void activeContext.close()
  }, [stopBgmSounds, stopKeySounds, stopSource])

  useEffect(() => {
    scheduledRef.current.clear()
  }, [difficulty, project.difficulties])

  return {
    playing, position, speed, loop, musicMuted, keySoundStatus, autoMusic, autoMusicLoading, duration,
    bgmEventCount: bmsBgmSchedule.length,
    musicStart: effectiveMusic?.startTime ?? 0,
    musicDuration: effectiveMusic?.buffer.duration ?? 0,
    play, pause, stop, seek, beginScrub, scrubTo, endScrub,
    setSpeed, setLoop, setMusicMuted, triggerKeySound, triggerLane, triggerNote,
  }
}
