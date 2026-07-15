import {
  ArrowRight,
  ArrowRightLeft,
  Binary,
  Combine,
  ListChecks,
  Palette,
  Plus,
  Trash2,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { PM3_AUXILIARY_TRACK_IDS, PM3_INPUT_TRACK_BY_LANE, TRACK_COLOR_PALETTE } from '../constants'
import type { TickRange } from '../store'
import type { Lane } from '../types'

interface TrackContextMenuProps {
  x: number
  y: number
  lane: Lane
  lanes: Lane[]
  maxPulse: number
  laneNoteCount: number
  selectedCount: number
  canRemove: boolean
  onClose: () => void
  onSelectLane: (range: TickRange | null) => void
  onCreateAndMove: (range: TickRange | null) => void
  onMove: (targetLaneId: number, range: TickRange | null) => void
  onSwap: (targetLaneId: number, range: TickRange | null) => void
  onMerge: (targetLaneId: number, range: TickRange | null) => void
  onSetColor: (color: string) => void
  onSetPm3Track: (trackId: number | null) => void
  onDeleteSelected: () => void
  onRemove: () => void
}

export function TrackContextMenu({
  x,
  y,
  lane,
  lanes,
  maxPulse,
  laneNoteCount,
  selectedCount,
  canRemove,
  onClose,
  onSelectLane,
  onCreateAndMove,
  onMove,
  onSwap,
  onMerge,
  onSetColor,
  onSetPm3Track,
  onDeleteSelected,
  onRemove,
}: TrackContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null)
  const targets = useMemo(() => lanes.filter((item) => item.id !== lane.id), [lane.id, lanes])
  const [targetLaneId, setTargetLaneId] = useState(targets[0]?.id ?? lane.id)
  const [limitRange, setLimitRange] = useState(false)
  const [start, setStart] = useState(0)
  const [end, setEnd] = useState(maxPulse)
  const range = limitRange ? { start, end } : null
  const left = Math.max(8, Math.min(x, window.innerWidth - 294))
  const menuHeight = Math.min(604, window.innerHeight - 16)
  const top = Math.max(8, Math.min(y, window.innerHeight - menuHeight - 8))
  const pm3Extension = lane.extensions.pm3 && typeof lane.extensions.pm3 === 'object' && !Array.isArray(lane.extensions.pm3)
    ? lane.extensions.pm3 as Record<string, unknown>
    : null
  const currentPm3Track = typeof pm3Extension?.track_id === 'number' ? pm3Extension.track_id : null
  const usedPm3Tracks = new Map(lanes.flatMap((item) => {
    if (item.id === lane.id || item.kind !== 'auxiliary') return []
    const extension = item.extensions.pm3 && typeof item.extensions.pm3 === 'object' && !Array.isArray(item.extensions.pm3)
      ? item.extensions.pm3 as Record<string, unknown>
      : null
    return typeof extension?.track_id === 'number' ? [[extension.track_id, item.display_name] as const] : []
  }))

  useEffect(() => {
    menuRef.current?.focus()
    const closeFromOutside = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) onClose()
    }
    window.addEventListener('pointerdown', closeFromOutside)
    return () => window.removeEventListener('pointerdown', closeFromOutside)
  }, [onClose])

  const run = (action: () => void) => {
    action()
    onClose()
  }

  return (
    <div
      ref={menuRef}
      className="track-context-menu"
      style={{ left, top }}
      role="menu"
      tabIndex={-1}
      aria-label={`${lane.display_name} Track 操作`}
      onContextMenu={(event) => event.preventDefault()}
      onKeyDown={(event) => { if (event.key === 'Escape') onClose() }}
    >
      <header>
        <i style={{ background: lane.color }} />
        <span><strong>{lane.display_name}</strong><small>Lane {lane.id} · {laneNoteCount} 音符</small></span>
        <button type="button" onClick={onClose} title="关闭"><X size={14} /></button>
      </header>

      <div className="track-menu-colors">
        <span><Palette size={13} />Track 颜色</span>
        <div>
          {TRACK_COLOR_PALETTE.map((color, index) => (
            <button
              key={color}
              type="button"
              className={lane.color.toLowerCase() === color ? 'selected' : ''}
              style={{ '--track-swatch': color } as CSSProperties}
              aria-label={`颜色 ${index + 1} ${color}`}
              aria-pressed={lane.color.toLowerCase() === color}
              title={color}
              onClick={() => run(() => onSetColor(color))}
            />
          ))}
        </div>
      </div>

      <div className="track-menu-pm3">
        <span><Binary size={13} />PM3 Track</span>
        {lane.kind === 'input' ? (
          <strong>输入 Track {PM3_INPUT_TRACK_BY_LANE[lane.id]}</strong>
        ) : (
          <select
            aria-label="PM3 Track 分类"
            value={currentPm3Track ?? ''}
            onChange={(event) => onSetPm3Track(event.target.value === '' ? null : Number(event.target.value))}
          >
            <option value="">待分类</option>
            {PM3_AUXILIARY_TRACK_IDS.map((trackId) => (
              <option key={trackId} value={trackId} disabled={usedPm3Tracks.has(trackId)}>
                辅助 Track {trackId}{usedPm3Tracks.has(trackId) ? ` · ${usedPm3Tracks.get(trackId)}` : ''}
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="track-menu-range">
        <label>
          <input type="checkbox" checked={limitRange} onChange={(event) => setLimitRange(event.target.checked)} />
          限定 Tick 区间
        </label>
        <div>
          <input aria-label="起始 Tick" type="number" min={0} value={start} disabled={!limitRange} onChange={(event) => setStart(Number(event.target.value))} />
          <span>至</span>
          <input aria-label="结束 Tick" type="number" min={0} value={end} disabled={!limitRange} onChange={(event) => setEnd(Number(event.target.value))} />
        </div>
      </div>

      <button type="button" role="menuitem" onClick={() => run(() => onSelectLane(range))}>
        <ListChecks size={14} /><span>选择本 Track 音符</span>
      </button>
      <button type="button" role="menuitem" onClick={() => run(() => onCreateAndMove(range))}>
        <Plus size={14} /><span>{laneNoteCount ? '新建匿名 Track 并迁移' : '新建空匿名 Track'}</span>
      </button>

      <div className="track-menu-target">
        <span>目标 Track</span>
        <select value={targetLaneId} onChange={(event) => setTargetLaneId(Number(event.target.value))}>
          {targets.map((target) => <option key={target.id} value={target.id}>{target.display_name}</option>)}
        </select>
      </div>

      <button type="button" role="menuitem" disabled={!targets.length || !laneNoteCount} onClick={() => run(() => onMove(targetLaneId, range))}>
        <ArrowRight size={14} /><span>迁移到目标 Track</span>
      </button>
      <button type="button" role="menuitem" disabled={!targets.length} onClick={() => run(() => onSwap(targetLaneId, range))}>
        <ArrowRightLeft size={14} /><span>与目标 Track 交换</span>
      </button>
      <button type="button" role="menuitem" disabled={!targets.length || !laneNoteCount} onClick={() => run(() => onMerge(targetLaneId, range))}>
        <Combine size={14} /><span>合并到目标并按 Tick 去重</span>
      </button>

      {(selectedCount > 0 || canRemove) && <div className="track-menu-separator" />}
      {selectedCount > 0 && (
        <button type="button" role="menuitem" className="danger" onClick={() => run(onDeleteSelected)}>
          <Trash2 size={14} /><span>删除已选音符</span><kbd>Del</kbd>
        </button>
      )}
      {canRemove && (
        <button type="button" role="menuitem" className="danger" onClick={() => run(onRemove)}>
          <Trash2 size={14} /><span>删除空 Track</span>
        </button>
      )}
    </div>
  )
}
