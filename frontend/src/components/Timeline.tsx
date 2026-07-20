import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { GripVertical, Volume2, VolumeX } from 'lucide-react'
import { useEditorStore } from '../store'
import { createTimingIndex, pulseToSeconds, secondsToPulse, snapPulseDelta } from '../timing'
import { barNumberAtPulse, buildTimelineGrid } from '../timelineGrid'
import type { Note, ValidationIssue } from '../types'
import { TrackContextMenu } from './TrackContextMenu'

const DEFAULT_LABEL_WIDTH = 168
const MIN_LABEL_WIDTH = 118
const MAX_LABEL_WIDTH = 320
const LABEL_WIDTH_STORAGE_KEY = 'bmson2pm.timeline-label-width.v1'
const WAVE_HEIGHT = 68
const RULER_HEIGHT = 30
const LANE_HEIGHT = 54

interface Point { x: number; y: number }
interface DragState {
  mode: 'notes' | 'resize' | 'marquee' | 'playhead'
  start: Point
  current: Point
  noteId?: string
}

interface HitResult { note: Note; part: 'body' | 'tail' }
interface TrackMenuState { x: number; y: number; laneId: number }
interface LabelResizeState { pointerId: number; startX: number; startWidth: number }

interface TimelineProps {
  peaks: Float32Array | null
  waveformStart: number
  waveformDuration: number
  position: number
  playing: boolean
  keySoundStatus: { ready: number; total: number; failed: number }
  hasMusic: boolean
  musicMuted: boolean
  issues: ValidationIssue[]
  onSeek: (seconds: number) => void
  onScrubStart: () => void
  onScrub: (seconds: number) => void
  onScrubEnd: () => void
  onMusicMute: (muted: boolean) => void
  onTriggerLane: (laneId: number) => void
  onTriggerNote: (note: Note) => void
}

export function Timeline({
  peaks,
  waveformStart,
  waveformDuration,
  position,
  playing,
  keySoundStatus,
  hasMusic,
  musicMuted,
  issues,
  onSeek,
  onScrubStart,
  onScrub,
  onScrubEnd,
  onMusicMute,
  onTriggerLane,
  onTriggerNote,
}: TimelineProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const labelResizeRef = useRef<LabelResizeState | null>(null)
  const [size, setSize] = useState({ width: 1000, height: WAVE_HEIGHT + RULER_HEIGHT + LANE_HEIGHT * 6 })
  const [labelWidth, setLabelWidth] = useState(() => {
    try {
      const stored = window.localStorage.getItem(LABEL_WIDTH_STORAGE_KEY)
      const saved = stored === null ? Number.NaN : Number(stored)
      return Number.isFinite(saved) ? Math.min(MAX_LABEL_WIDTH, Math.max(MIN_LABEL_WIDTH, saved)) : DEFAULT_LABEL_WIDTH
    } catch {
      return DEFAULT_LABEL_WIDTH
    }
  })
  const [drag, setDrag] = useState<DragState | null>(null)
  const [playheadHovered, setPlayheadHovered] = useState(false)
  const [resizingLabels, setResizingLabels] = useState(false)
  const [trackMenu, setTrackMenu] = useState<TrackMenuState | null>(null)
  const autoFollowingRef = useRef(false)
  const manualScrollUntilRef = useRef(0)
  const project = useEditorStore((state) => state.project)
  const difficulty = useEditorStore((state) => state.activeDifficulty)
  const referenceDifficulty = useEditorStore((state) => state.referenceDifficulty)
  const activeLaneId = useEditorStore((state) => state.activeLaneId)
  const selectedIds = useEditorStore((state) => state.selectedIds)
  const tool = useEditorStore((state) => state.tool)
  const quantizeDivisor = useEditorStore((state) => state.quantizeDivisor)
  const zoom = useEditorStore((state) => state.zoom)
  const scrollPulse = useEditorStore((state) => state.scrollPulse)
  const setScrollPulse = useEditorStore((state) => state.setScrollPulse)
  const setZoom = useEditorStore((state) => state.setZoom)
  const setActiveLane = useEditorStore((state) => state.setActiveLane)
  const addNote = useEditorStore((state) => state.addNote)
  const selectOnly = useEditorStore((state) => state.selectOnly)
  const toggleSelection = useEditorStore((state) => state.toggleSelection)
  const selectMany = useEditorStore((state) => state.selectMany)
  const deleteSelected = useEditorStore((state) => state.deleteSelected)
  const toggleLaneMute = useEditorStore((state) => state.toggleLaneMute)
  const setLaneColor = useEditorStore((state) => state.setLaneColor)
  const setLanePm3Track = useEditorStore((state) => state.setLanePm3Track)
  const selectLaneNotes = useEditorStore((state) => state.selectLaneNotes)
  const createAnonymousLane = useEditorStore((state) => state.createAnonymousLane)
  const moveLaneNotes = useEditorStore((state) => state.moveLaneNotes)
  const swapLaneNotes = useEditorStore((state) => state.swapLaneNotes)
  const mergeLaneInto = useEditorStore((state) => state.mergeLaneInto)
  const removeEmptyAnonymousLane = useEditorStore((state) => state.removeEmptyAnonymousLane)
  const beginNoteTransaction = useEditorStore((state) => state.beginNoteTransaction)
  const previewMoveTransaction = useEditorStore((state) => state.previewMoveTransaction)
  const previewResizeTransaction = useEditorStore((state) => state.previewResizeTransaction)
  const finishNoteTransaction = useEditorStore((state) => state.finishNoteTransaction)
  const cancelNoteTransaction = useEditorStore((state) => state.cancelNoteTransaction)
  const chart = project.difficulties[difficulty]
  const notes = chart.notes
  const referenceNotes = referenceDifficulty ? project.difficulties[referenceDifficulty].notes : []
  const resolution = project.timing.resolution
  const canvasHeight = WAVE_HEIGHT + RULER_HEIGHT + LANE_HEIGHT * project.lanes.length
  const timingIndex = useMemo(() => createTimingIndex(project.timing), [project.timing])

  useEffect(() => {
    const target = wrapRef.current
    if (!target) return
    let frame = 0
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.max(600, Math.floor(entry.contentRect.width))
      window.cancelAnimationFrame(frame)
      frame = window.requestAnimationFrame(() => {
        setSize((current) => (
          current.width === width && current.height === canvasHeight
            ? current
            : { width, height: canvasHeight }
        ))
      })
    })
    observer.observe(target)
    return () => {
      observer.disconnect()
      window.cancelAnimationFrame(frame)
    }
  }, [canvasHeight])

  useEffect(() => {
    try {
      window.localStorage.setItem(LABEL_WIDTH_STORAGE_KEY, String(labelWidth))
    } catch {
      // Local storage may be unavailable in privacy-restricted environments.
    }
  }, [labelWidth])

  const pulseToX = useCallback((pulse: number) => labelWidth + (pulse - scrollPulse) / resolution * zoom, [labelWidth, resolution, scrollPulse, zoom])
  const xToPulse = useCallback((x: number) => scrollPulse + (x - labelWidth) / zoom * resolution, [labelWidth, resolution, scrollPulse, zoom])
  const laneTop = (laneIndex: number) => WAVE_HEIGHT + RULER_HEIGHT + laneIndex * LANE_HEIGHT
  const playheadX = pulseToX(secondsToPulse(project, position, timingIndex))
  const secondsAtX = useCallback((x: number) => Math.max(
    0,
    pulseToSeconds(project, Math.max(0, xToPulse(Math.min(size.width, Math.max(labelWidth, x)))), timingIndex),
  ), [project, size.width, timingIndex, xToPulse])
  const hitsPlayhead = useCallback((point: Point) => (
    point.y >= WAVE_HEIGHT - 9
    && playheadX >= labelWidth
    && playheadX <= size.width
    && Math.abs(point.x - playheadX) <= 10
  ), [labelWidth, playheadX, size.width])

  useEffect(() => {
    if (!playing) {
      autoFollowingRef.current = false
      return
    }
    if (drag?.mode === 'playhead' || Date.now() < manualScrollUntilRef.current) return
    const visiblePulses = Math.max(resolution, (size.width - labelWidth) / zoom * resolution)
    const pulse = secondsToPulse(project, position, timingIndex)
    const visibleEnd = scrollPulse + visiblePulses
    if (pulse < scrollPulse || pulse > scrollPulse + visiblePulses * 0.78) {
      autoFollowingRef.current = true
    }
    if (!autoFollowingRef.current) return
    const anchor = pulse < scrollPulse ? 0.18 : 0.72
    const next = Math.max(0, pulse - visiblePulses * anchor)
    if (Math.abs(next - scrollPulse) >= 0.5) setScrollPulse(next)
  }, [drag?.mode, labelWidth, playing, position, project, resolution, scrollPulse, setScrollPulse, size.width, timingIndex, zoom])

  const hitTest = useCallback((point: Point): HitResult | null => {
    for (let index = notes.length - 1; index >= 0; index -= 1) {
      const note = notes[index]
      const laneIndex = project.lanes.findIndex((lane) => lane.id === note.lane_id)
      if (laneIndex < 0) continue
      const x = pulseToX(note.pulse)
      const tailX = pulseToX(note.pulse + note.length)
      const y = laneTop(laneIndex) + LANE_HEIGHT / 2
      if (note.length > 0 && Math.abs(point.x - tailX) <= 9 && Math.abs(point.y - y) <= 12) {
        return { note, part: 'tail' }
      }
      if (note.length > 0 && point.x >= Math.min(x, tailX) && point.x <= Math.max(x, tailX) && Math.abs(point.y - y) <= 7) {
        return { note, part: 'body' }
      }
      if (Math.abs(point.x - x) <= 11 && Math.abs(point.y - y) <= 16) return { note, part: 'body' }
    }
    return null
  }, [notes, project.lanes, pulseToX])

  const issueSeverity = useMemo(() => {
    const result = new Map<string, ValidationIssue['severity']>()
    for (const issue of issues) {
      if (!issue.note_id) continue
      const previous = result.get(issue.note_id)
      if (issue.severity === 'error' || !previous) result.set(issue.note_id, issue.severity)
    }
    return result
  }, [issues])

  const pointFromEvent = (event: React.PointerEvent<HTMLCanvasElement> | React.MouseEvent<HTMLCanvasElement>): Point => {
    const rect = event.currentTarget.getBoundingClientRect()
    return { x: event.clientX - rect.left, y: event.clientY - rect.top }
  }

  const laneIndexAt = (y: number) => Math.floor((y - WAVE_HEIGHT - RULER_HEIGHT) / LANE_HEIGHT)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    canvas.width = size.width * dpr
    canvas.height = size.height * dpr
    canvas.style.width = `${size.width}px`
    canvas.style.height = `${size.height}px`
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.scale(dpr, dpr)
    ctx.clearRect(0, 0, size.width, size.height)
    ctx.fillStyle = '#151b1f'
    ctx.fillRect(0, 0, size.width, size.height)

    // Waveform track
    ctx.fillStyle = '#111619'
    ctx.fillRect(0, 0, size.width, WAVE_HEIGHT)
    ctx.fillStyle = '#1b2428'
    ctx.fillRect(0, 0, labelWidth, WAVE_HEIGHT)
    const center = WAVE_HEIGHT / 2
    if (peaks?.length) {
      ctx.beginPath()
      ctx.strokeStyle = '#5a9d95'
      ctx.lineWidth = 1
      for (let x = labelWidth; x < size.width; x += 2) {
        const pulse = xToPulse(x)
        const seconds = pulseToSeconds(project, pulse, timingIndex)
        const relativeSeconds = seconds - waveformStart
        if (relativeSeconds < 0 || relativeSeconds > waveformDuration) continue
        const peakIndex = Math.floor(relativeSeconds / Math.max(waveformDuration, 0.01) * peaks.length)
        const peak = peaks[Math.min(peaks.length - 1, Math.max(0, peakIndex))] ?? 0
        ctx.moveTo(x, center - peak * 25)
        ctx.lineTo(x, center + peak * 25)
      }
      ctx.stroke()
    } else {
      ctx.beginPath()
      ctx.strokeStyle = '#304147'
      for (let x = labelWidth; x < size.width; x += 3) {
        const wave = (Math.sin(x * 0.051) + Math.sin(x * 0.017) * 0.5) * 6
        ctx.moveTo(x, center - Math.abs(wave))
        ctx.lineTo(x, center + Math.abs(wave))
      }
      ctx.stroke()
    }

    // Ruler and Track backgrounds
    ctx.fillStyle = '#101518'
    ctx.fillRect(0, WAVE_HEIGHT, size.width, RULER_HEIGHT)
    project.lanes.forEach((lane, index) => {
      const top = laneTop(index)
      const active = lane.id === activeLaneId
      ctx.fillStyle = active ? '#20302f' : index % 2 === 0 ? '#182025' : '#151c20'
      ctx.fillRect(labelWidth, top, size.width - labelWidth, LANE_HEIGHT)
      ctx.fillStyle = active ? '#263638' : '#1d262a'
      ctx.fillRect(0, top, labelWidth, LANE_HEIGHT)
      ctx.strokeStyle = '#2a3439'
      ctx.beginPath()
      ctx.moveTo(0, top + LANE_HEIGHT - 0.5)
      ctx.lineTo(size.width, top + LANE_HEIGHT - 0.5)
      ctx.stroke()
    })

    // Draw the timeline grid after Track fills so it remains visible in every lane.
    const gridLines = buildTimelineGrid({
      visibleStart: Math.max(0, scrollPulse),
      visibleEnd: xToPulse(size.width),
      resolution,
      divisor: quantizeDivisor,
      zoom,
      barLines: project.timing.bar_lines,
    })
    ctx.save()
    ctx.beginPath()
    ctx.rect(labelWidth, WAVE_HEIGHT, size.width - labelWidth, size.height - WAVE_HEIGHT)
    ctx.clip()
    for (const line of gridLines) {
      const x = Math.round(pulseToX(line.pulse)) + 0.5
      ctx.beginPath()
      ctx.strokeStyle = line.kind === 'bar' ? '#455158' : line.kind === 'beat' ? '#303a3f' : '#242c30'
      ctx.lineWidth = line.kind === 'bar' ? 1.2 : 1
      ctx.moveTo(x, line.kind === 'bar' ? WAVE_HEIGHT : WAVE_HEIGHT + RULER_HEIGHT)
      ctx.lineTo(x, size.height)
      ctx.stroke()
      if (line.kind === 'bar' && line.label) {
        ctx.fillStyle = '#a7b4ba'
        ctx.font = '10px Inter, system-ui, sans-serif'
        ctx.fillText(line.label, x + 5, WAVE_HEIGHT + 19)
      } else if (line.kind === 'beat' && line.label && zoom >= 58) {
        ctx.fillStyle = '#5f6d73'
        ctx.font = '9px Inter, system-ui, sans-serif'
        ctx.fillText(line.label, x + 3, WAVE_HEIGHT + 19)
      }
    }
    ctx.restore()

    const drawNote = (note: Note, alpha: number, reference = false) => {
      const x = pulseToX(note.pulse)
      const endX = pulseToX(note.pulse + note.length)
      if (endX < labelWidth - 16 || x > size.width + 16) return
      const laneIndex = project.lanes.findIndex((lane) => lane.id === note.lane_id)
      if (laneIndex < 0) return
      const lane = project.lanes[laneIndex]
      const y = laneTop(laneIndex) + LANE_HEIGHT / 2
      const selected = selectedIds.has(note.id) && !reference
      ctx.save()
      ctx.globalAlpha = alpha
      if (reference) {
        ctx.strokeStyle = '#91a2aa'
        ctx.lineWidth = 1
        ctx.setLineDash([3, 2])
        ctx.strokeRect(x - 6, y - 10, 12, 20)
      } else {
        ctx.shadowColor = selected ? lane.color : 'transparent'
        ctx.shadowBlur = selected ? 10 : 0
        ctx.fillStyle = lane.color
        ctx.beginPath()
        ctx.roundRect(x - 6, y - 14, 12, 28, 3)
        ctx.fill()
        ctx.fillStyle = 'rgba(255,255,255,.68)'
        ctx.fillRect(x - 2, y - 9, 2, 18)
        if (note.length > 0) {
          ctx.strokeStyle = lane.color
          ctx.lineWidth = 4
          ctx.beginPath()
          ctx.moveTo(x + 6, y)
          ctx.lineTo(endX, y)
          ctx.stroke()
          if (selected) {
            ctx.fillStyle = '#ffffff'
            ctx.fillRect(endX - 3, y - 5, 6, 10)
          }
        }
        if (selected) {
          ctx.shadowBlur = 0
          ctx.strokeStyle = '#ffffff'
          ctx.lineWidth = 1.5
          ctx.strokeRect(x - 9, y - 17, 18, 34)
        }
        const severity = issueSeverity.get(note.id)
        if (severity) {
          ctx.shadowBlur = 0
          ctx.strokeStyle = severity === 'error' ? '#ff5d6c' : '#f2aa4f'
          ctx.lineWidth = 2
          ctx.strokeRect(x - 11, y - 19, Math.max(22, endX - x + 13), 38)
        }
      }
      ctx.restore()
    }
    referenceNotes.forEach((note) => drawNote(note, 0.34, true))
    notes.forEach((note) => drawNote(note, project.lanes.find((lane) => lane.id === note.lane_id)?.muted ? 0.28 : 1))

    if (drag?.mode === 'marquee') {
      const left = Math.min(drag.start.x, drag.current.x)
      const top = Math.min(drag.start.y, drag.current.y)
      const width = Math.abs(drag.current.x - drag.start.x)
      const height = Math.abs(drag.current.y - drag.start.y)
      ctx.fillStyle = 'rgba(64,196,180,.10)'
      ctx.fillRect(left, top, width, height)
      ctx.strokeStyle = '#40c4b4'
      ctx.setLineDash([4, 3])
      ctx.strokeRect(left + 0.5, top + 0.5, width, height)
      ctx.setLineDash([])
    }

    const playPulse = secondsToPulse(project, position, timingIndex)
    const playX = pulseToX(playPulse)
    if (playX >= labelWidth && playX <= size.width) {
      ctx.strokeStyle = '#ff5d6c'
      ctx.lineWidth = drag?.mode === 'playhead' || playheadHovered ? 2.5 : 1.5
      ctx.beginPath()
      ctx.moveTo(playX, WAVE_HEIGHT)
      ctx.lineTo(playX, size.height)
      ctx.stroke()
      ctx.fillStyle = '#ff5d6c'
      ctx.beginPath()
      const handleSize = drag?.mode === 'playhead' || playheadHovered ? 7 : 5
      ctx.moveTo(playX - handleSize, WAVE_HEIGHT)
      ctx.lineTo(playX + handleSize, WAVE_HEIGHT)
      ctx.lineTo(playX, WAVE_HEIGHT + handleSize + 2)
      ctx.closePath()
      ctx.fill()
    }
  }, [activeLaneId, drag, issueSeverity, notes, peaks, playheadHovered, position, project, pulseToX, quantizeDivisor, referenceNotes, resolution, selectedIds, size, timingIndex, waveformDuration, waveformStart, xToPulse, zoom])

  const handlePointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const point = pointFromEvent(event)
    event.currentTarget.setPointerCapture(event.pointerId)
    if (hitsPlayhead(point)) {
      event.preventDefault()
      onScrubStart()
      onScrub(secondsAtX(point.x))
      setDrag({ mode: 'playhead', start: point, current: point })
      setPlayheadHovered(true)
      return
    }
    const laneIndex = laneIndexAt(point.y)
    if (laneIndex < 0) {
      if (point.y >= WAVE_HEIGHT) {
        onSeek(Math.max(0, pulseToSeconds(project, xToPulse(point.x), timingIndex)))
      }
      return
    }
    if (laneIndex >= project.lanes.length) return
    const laneId = project.lanes[laneIndex].id
    setActiveLane(laneId)
    if (point.x < labelWidth) return
    const hit = hitTest(point)
    if (tool === 'draw') {
      if (hit) {
        selectOnly(hit.note.id)
        onTriggerNote(hit.note)
        return
      }
      addNote(laneId, xToPulse(point.x))
      onTriggerLane(laneId)
      return
    }
    if (hit) {
      onTriggerNote(hit.note)
      let dragIds: string[]
      if (event.shiftKey || event.metaKey || event.ctrlKey) {
        if (selectedIds.has(hit.note.id)) {
          toggleSelection(hit.note.id)
          return
        }
        toggleSelection(hit.note.id)
        dragIds = [...selectedIds, hit.note.id]
      } else {
        if (!selectedIds.has(hit.note.id)) selectOnly(hit.note.id)
        dragIds = selectedIds.has(hit.note.id) ? [...selectedIds] : [hit.note.id]
      }
      beginNoteTransaction(hit.part === 'tail' ? [hit.note.id] : dragIds)
      setDrag({ mode: hit.part === 'tail' ? 'resize' : 'notes', start: point, current: point, noteId: hit.note.id })
    } else {
      if (!event.shiftKey) selectOnly(null)
      setDrag({ mode: 'marquee', start: point, current: point })
    }
  }

  const handlePointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const point = pointFromEvent(event)
    if (!drag) {
      setPlayheadHovered(hitsPlayhead(point))
      return
    }
    setDrag({ ...drag, current: point })
    if (drag.mode === 'playhead') {
      onScrub(secondsAtX(point.x))
    } else if (drag.mode === 'notes') {
      previewMoveTransaction(
        snapPulseDelta(xToPulse(point.x) - xToPulse(drag.start.x), resolution, quantizeDivisor),
        laneIndexAt(point.y) - laneIndexAt(drag.start.y),
      )
    } else if (drag.mode === 'resize' && drag.noteId) {
      const note = notes.find((item) => item.id === drag.noteId)
      if (note) {
        previewResizeTransaction(
          note.id,
          Math.max(0, snapPulseDelta(xToPulse(point.x) - note.pulse, resolution, quantizeDivisor)),
        )
      }
    }
  }

  const handlePointerUp = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!drag) return
    const point = pointFromEvent(event)
    if (drag.mode === 'playhead') {
      onScrub(secondsAtX(point.x))
      onScrubEnd()
    } else if (drag.mode === 'notes' || drag.mode === 'resize') {
      finishNoteTransaction()
    } else {
      const left = Math.min(drag.start.x, point.x)
      const right = Math.max(drag.start.x, point.x)
      const top = Math.min(drag.start.y, point.y)
      const bottom = Math.max(drag.start.y, point.y)
      const ids = notes.filter((note) => {
        const laneIndex = project.lanes.findIndex((lane) => lane.id === note.lane_id)
        const x = pulseToX(note.pulse)
        const y = laneTop(laneIndex) + LANE_HEIGHT / 2
        return x >= left && x <= right && y >= top && y <= bottom
      }).map((note) => note.id)
      selectMany(ids, event.shiftKey)
    }
    setDrag(null)
  }

  const handleWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault()
      event.stopPropagation()
      manualScrollUntilRef.current = Date.now() + 1200
      autoFollowingRef.current = false
      setZoom(zoom * (event.deltaY > 0 ? 0.9 : 1.1))
    } else if (
      Math.abs(event.deltaX) > Math.abs(event.deltaY)
      || event.shiftKey
      || (wrapRef.current?.scrollHeight ?? 0) <= (wrapRef.current?.clientHeight ?? 0)
    ) {
      event.preventDefault()
      event.stopPropagation()
      manualScrollUntilRef.current = Date.now() + 1200
      autoFollowingRef.current = false
      setScrollPulse(scrollPulse + (event.deltaX + event.deltaY) / zoom * resolution * 0.75)
    }
  }

  const clampLabelWidth = useCallback((width: number) => (
    Math.round(Math.min(MAX_LABEL_WIDTH, Math.max(MIN_LABEL_WIDTH, Math.min(width, size.width - 260))))
  ), [size.width])

  const handleLabelResizeStart = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.stopPropagation()
    event.currentTarget.setPointerCapture(event.pointerId)
    labelResizeRef.current = { pointerId: event.pointerId, startX: event.clientX, startWidth: labelWidth }
    setResizingLabels(true)
  }

  const handleLabelResizeMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const resize = labelResizeRef.current
    if (!resize || resize.pointerId !== event.pointerId) return
    setLabelWidth(clampLabelWidth(resize.startWidth + event.clientX - resize.startX))
  }

  const handleLabelResizeEnd = (event: React.PointerEvent<HTMLDivElement>) => {
    if (labelResizeRef.current?.pointerId !== event.pointerId) return
    labelResizeRef.current = null
    setResizingLabels(false)
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
  }

  const openTrackMenu = (laneId: number, x: number, y: number) => {
    setActiveLane(laneId)
    setTrackMenu({ x, y, laneId })
  }

  const handleContextMenu = (event: React.MouseEvent<HTMLCanvasElement>) => {
    event.preventDefault()
    const point = pointFromEvent(event)
    const hit = hitTest(point)
    const laneIndex = hit
      ? project.lanes.findIndex((lane) => lane.id === hit.note.lane_id)
      : laneIndexAt(point.y)
    if (laneIndex < 0 || laneIndex >= project.lanes.length) return
    const laneId = project.lanes[laneIndex].id
    if (hit && !selectedIds.has(hit.note.id)) selectOnly(hit.note.id)
    openTrackMenu(laneId, event.clientX, event.clientY)
  }

  useEffect(() => {
    const openFromKeyboard = (event: KeyboardEvent) => {
      if (event.key !== 'ContextMenu' && !(event.shiftKey && event.key === 'F10')) return
      const target = event.target as HTMLElement
      if (target.matches('input, textarea, select, [contenteditable="true"]')) return
      const laneId = activeLaneId ?? project.lanes[0]?.id
      const laneIndex = project.lanes.findIndex((lane) => lane.id === laneId)
      const canvas = canvasRef.current
      if (!canvas || laneIndex < 0) return
      event.preventDefault()
      const rect = canvas.getBoundingClientRect()
      setTrackMenu({
        x: rect.left + Math.min(labelWidth - 10, rect.width / 2),
        y: Math.max(8, Math.min(window.innerHeight - 40, rect.top + laneTop(laneIndex) + LANE_HEIGHT / 2)),
        laneId,
      })
    }
    window.addEventListener('keydown', openFromKeyboard)
    return () => window.removeEventListener('keydown', openFromKeyboard)
  }, [activeLaneId, labelWidth, project.lanes])

  useEffect(() => {
    if (!drag) return
    const cancelOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (drag.mode === 'playhead') onScrubEnd()
      else cancelNoteTransaction()
      setDrag(null)
    }
    window.addEventListener('keydown', cancelOnEscape)
    return () => window.removeEventListener('keydown', cancelOnEscape)
  }, [cancelNoteTransaction, drag, onScrubEnd])

  const cancelPointer = () => {
    if (drag?.mode === 'playhead') onScrubEnd()
    else cancelNoteTransaction()
    setDrag(null)
  }

  const visibleEndPulse = scrollPulse + Math.max(0, (size.width - labelWidth) / zoom * resolution)
  const startBar = barNumberAtPulse(scrollPulse, resolution, project.timing.bar_lines)
  const endBar = barNumberAtPulse(visibleEndPulse, resolution, project.timing.bar_lines)

  return (
    <div className="timeline-shell" ref={wrapRef} onWheel={handleWheel}>
      <div className="timeline-meta" style={{ left: labelWidth + 10 }}>
        <span>小节 {startBar}–{endBar}</span>
        <span>{notes.length} 事件</span>
        <span>{project.lanes.length} Tracks</span>
        <span>{Math.round(zoom)} px/beat</span>
      </div>
      <canvas
        ref={canvasRef}
        className={`timeline-canvas tool-${tool}${playheadHovered || drag?.mode === 'playhead' ? ' playhead-active' : ''}`}
        aria-label="谱面轨道时间轴"
        data-key-sounds-ready={keySoundStatus.ready}
        data-key-sounds-total={keySoundStatus.total}
        data-key-sounds-failed={keySoundStatus.failed}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={cancelPointer}
        onPointerLeave={() => { if (!drag) setPlayheadHovered(false) }}
        onContextMenu={handleContextMenu}
      />
      <div className={`timeline-track-headers${resizingLabels ? ' resizing' : ''}`} style={{ width: labelWidth }}>
        <div className="music-track-header">
          <span className="track-header-copy">
            <strong>MUSIC / WAVEFORM</strong>
            <small>{project.timing.initial_bpm} BPM</small>
          </span>
          {hasMusic && (
            <button
              type="button"
              className={musicMuted ? 'muted' : ''}
              onClick={() => onMusicMute(!musicMuted)}
              aria-pressed={musicMuted}
              title={musicMuted ? '取消音乐静音' : '音乐静音'}
            >
              {musicMuted ? <VolumeX size={14} /> : <Volume2 size={14} />}
            </button>
          )}
        </div>
        <div className="timeline-track-ruler" />
        {project.lanes.map((lane) => {
          const pm3 = lane.extensions.pm3 && typeof lane.extensions.pm3 === 'object' && !Array.isArray(lane.extensions.pm3)
            ? lane.extensions.pm3 as Record<string, unknown>
            : null
          const pm3Track = typeof pm3?.track_id === 'number' ? pm3.track_id : null
          const detail = lane.kind === 'auxiliary'
            ? `AUX ${pm3Track ?? '?'} · NON-SCORING`
            : lane.kind === 'anonymous'
              ? `ANON ${lane.id} · UNCLASSIFIED`
              : `INPUT ${lane.id} · ${lane.hand === 'left' ? 'LEFT' : lane.hand === 'right' ? 'RIGHT' : lane.hand === 'both' ? 'BOTH' : 'SINGLE'}`
          return (
            <div
              key={lane.id}
              className={`timeline-track-header ${lane.kind}${lane.id === activeLaneId ? ' active' : ''}${lane.muted ? ' muted' : ''}`}
              onClick={() => setActiveLane(lane.id)}
              onContextMenu={(event) => {
                event.preventDefault()
                openTrackMenu(lane.id, event.clientX, event.clientY)
              }}
            >
              <i className="track-color-stripe" style={{ background: lane.color }} />
              <span className="track-header-copy" title={lane.display_name}>
                <strong>{lane.display_name}</strong>
                <small>{detail}</small>
              </span>
              <button
                type="button"
                className={lane.muted ? 'muted' : ''}
                onPointerDown={(event) => event.stopPropagation()}
                onClick={(event) => {
                  event.stopPropagation()
                  toggleLaneMute(lane.id)
                }}
                aria-pressed={lane.muted}
                title={lane.muted ? `取消静音 ${lane.display_name}` : `静音 ${lane.display_name}`}
              >
                {lane.muted ? <VolumeX size={14} /> : <Volume2 size={14} />}
              </button>
            </div>
          )
        })}
      </div>
      <div
        className={`track-label-resizer${resizingLabels ? ' active' : ''}`}
        style={{ left: labelWidth - 4, height: canvasHeight }}
        role="separator"
        aria-label="调整 Track 名称区域宽度"
        aria-orientation="vertical"
        aria-valuemin={MIN_LABEL_WIDTH}
        aria-valuemax={MAX_LABEL_WIDTH}
        aria-valuenow={labelWidth}
        tabIndex={0}
        title="拖拽调整 Track 名称区域宽度；双击复位"
        onPointerDown={handleLabelResizeStart}
        onPointerMove={handleLabelResizeMove}
        onPointerUp={handleLabelResizeEnd}
        onPointerCancel={handleLabelResizeEnd}
        onDoubleClick={() => setLabelWidth(DEFAULT_LABEL_WIDTH)}
        onKeyDown={(event) => {
          if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
          event.preventDefault()
          setLabelWidth((width) => clampLabelWidth(width + (event.key === 'ArrowLeft' ? -8 : 8)))
        }}
      >
        <GripVertical size={12} />
      </div>
      {trackMenu && (() => {
        const lane = project.lanes.find((item) => item.id === trackMenu.laneId)
        if (!lane) return null
        const laneNotes = notes.filter((note) => note.lane_id === lane.id)
        const canRemove = lane.kind !== 'input' && !Object.values(project.difficulties)
          .some((difficultyChart) => difficultyChart.notes.some((note) => note.lane_id === lane.id))
        return (
          <TrackContextMenu
            x={trackMenu.x}
            y={trackMenu.y}
            lane={lane}
            lanes={project.lanes}
            maxPulse={Math.max(0, ...notes.map((note) => note.pulse + note.length))}
            laneNoteCount={laneNotes.length}
            selectedCount={selectedIds.size}
            canRemove={canRemove}
            onClose={() => setTrackMenu(null)}
            onSelectLane={(range) => selectLaneNotes(lane.id, range)}
            onCreateAndMove={(range) => createAnonymousLane(laneNotes.length ? lane.id : undefined, range)}
            onMove={(target, range) => moveLaneNotes(lane.id, target, range)}
            onSwap={(target, range) => swapLaneNotes(lane.id, target, range)}
            onMerge={(target, range) => mergeLaneInto(lane.id, target, range)}
            onSetColor={(color) => setLaneColor(lane.id, color)}
            onSetPm3Track={(trackId) => setLanePm3Track(lane.id, trackId)}
            onDeleteSelected={deleteSelected}
            onRemove={() => removeEmptyAnonymousLane(lane.id)}
          />
        )
      })()}
    </div>
  )
}
