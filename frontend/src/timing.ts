import type { BpmEvent, Note, SongProject, TimingMap } from './types'

interface TimingPoint {
  pulse: number
  secondsBefore: number
  secondsAfter: number
  bpmAfter: number
}

export interface TimingIndex {
  resolution: number
  initialBpm: number
  offsetSeconds: number
  points: TimingPoint[]
}

export function createTimingIndex(timing: TimingMap): TimingIndex {
  const bpmByPulse = new Map<number, number>()
  const stopsByPulse = new Map<number, number>()
  for (const event of timing.bpm_events) bpmByPulse.set(event.pulse, event.bpm)
  for (const event of timing.stop_events) {
    stopsByPulse.set(event.pulse, (stopsByPulse.get(event.pulse) ?? 0) + event.duration_pulses)
  }
  const eventPulses = [...new Set([...bpmByPulse.keys(), ...stopsByPulse.keys()])].sort((a, b) => a - b)
  const points: TimingPoint[] = []
  let currentPulse = 0
  let currentBpm = timing.initial_bpm
  let seconds = timing.chart_offset_ms / 1000
  for (const pulse of eventPulses) {
    seconds += pulsesToDuration(pulse - currentPulse, timing.resolution, currentBpm)
    const secondsBefore = seconds
    currentBpm = bpmByPulse.get(pulse) ?? currentBpm
    seconds += pulsesToDuration(stopsByPulse.get(pulse) ?? 0, timing.resolution, currentBpm)
    points.push({ pulse, secondsBefore, secondsAfter: seconds, bpmAfter: currentBpm })
    currentPulse = pulse
  }
  return {
    resolution: timing.resolution,
    initialBpm: timing.initial_bpm,
    offsetSeconds: timing.chart_offset_ms / 1000,
    points,
  }
}

export function pulseToSeconds(
  project: Pick<SongProject, 'timing'>,
  pulse: number,
  index = createTimingIndex(project.timing),
): number {
  const pointIndex = findLastIndex(index.points, (point) => point.pulse < pulse)
  if (pointIndex < 0) {
    return index.offsetSeconds + pulsesToDuration(pulse, index.resolution, index.initialBpm)
  }
  const point = index.points[pointIndex]
  return point.secondsAfter + pulsesToDuration(pulse - point.pulse, index.resolution, point.bpmAfter)
}

export function secondsToPulse(
  project: Pick<SongProject, 'timing'>,
  seconds: number,
  index = createTimingIndex(project.timing),
): number {
  if (seconds <= index.offsetSeconds) return 0
  const pointIndex = findLastIndex(index.points, (point) => point.secondsBefore < seconds)
  if (pointIndex < 0) {
    return Math.max(0, Math.round(durationToPulses(seconds - index.offsetSeconds, index.resolution, index.initialBpm)))
  }
  const point = index.points[pointIndex]
  if (seconds <= point.secondsAfter) return point.pulse
  return Math.max(0, Math.round(
    point.pulse + durationToPulses(seconds - point.secondsAfter, index.resolution, point.bpmAfter),
  ))
}

const pulsesToDuration = (pulses: number, resolution: number, bpm: number) => pulses / resolution * 60 / bpm
const durationToPulses = (seconds: number, resolution: number, bpm: number) => seconds * bpm / 60 * resolution

function findLastIndex<T>(values: T[], predicate: (value: T) => boolean): number {
  let low = 0
  let high = values.length - 1
  let result = -1
  while (low <= high) {
    const middle = Math.floor((low + high) / 2)
    if (predicate(values[middle])) {
      result = middle
      low = middle + 1
    } else {
      high = middle - 1
    }
  }
  return result
}

/** Return an integer tick for a rational grid index without accumulating float drift. */
export function gridPulseAt(index: number, resolution: number, divisor: number): number {
  return Math.round(index * resolution / Math.max(1, divisor))
}

export function snapPulse(pulse: number, resolution: number, divisor: number): number {
  const index = Math.round(pulse * Math.max(1, divisor) / resolution)
  return Math.max(0, gridPulseAt(index, resolution, divisor))
}

export function snapPulseDelta(delta: number, resolution: number, divisor: number): number {
  const index = Math.round(delta * Math.max(1, divisor) / resolution)
  return gridPulseAt(index, resolution, divisor)
}

export function formatTime(seconds: number): string {
  const safe = Math.max(0, seconds)
  const minutes = Math.floor(safe / 60)
  const rest = safe - minutes * 60
  return `${String(minutes).padStart(2, '0')}:${rest.toFixed(2).padStart(5, '0')}`
}

export interface ChartStats {
  total: number
  nps: number
  simultaneousRate: number
  leftRate: number
  rimRate: number
  minIntervalMs: number | null
  denseBar: number
  laneCounts: Record<number, number>
}

export function calculateStats(project: SongProject, notes: Note[]): ChartStats {
  const laneCounts: Record<number, number> = Object.fromEntries(project.lanes.map((lane) => [lane.id, 0]))
  for (const note of notes) laneCounts[note.lane_id] = (laneCounts[note.lane_id] ?? 0) + 1
  const groups = new Map<number, number>()
  const bars = new Map<number, number>()
  const timingIndex = createTimingIndex(project.timing)
  const times = notes.map((note) => pulseToSeconds(project, note.pulse, timingIndex)).sort((a, b) => a - b)
  for (const note of notes) {
    groups.set(note.pulse, (groups.get(note.pulse) ?? 0) + 1)
    const bar = Math.floor(note.pulse / (project.timing.resolution * 4)) + 1
    bars.set(bar, (bars.get(bar) ?? 0) + 1)
  }
  const simultaneousPulses = new Set(
    [...groups.entries()].filter(([, count]) => count > 1).map(([pulse]) => pulse),
  )
  const simultaneousLaneIds = new Set(
    project.lanes.filter((lane) => lane.hand === 'both').map((lane) => lane.id),
  )
  const simultaneous = notes.filter((note) => (
    simultaneousPulses.has(note.pulse) || simultaneousLaneIds.has(note.lane_id)
  )).length
  const intervals = times.slice(1).map((time, index) => time - times[index]).filter((value) => value > 0)
  const duration = Math.max(times.at(-1) ?? 0, 1)
  const denseBar = [...bars.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? 1
  const leftIds = new Set(project.lanes.filter((lane) => lane.code === 'small_left').map((lane) => lane.id))
  const smallIds = new Set(project.lanes.filter((lane) => lane.code.startsWith('small_')).map((lane) => lane.id))
  const smallNotes = notes.filter((note) => smallIds.has(note.lane_id))
  const rimIds = new Set(project.lanes.filter((lane) => lane.code.includes('rim')).map((lane) => lane.id))
  return {
    total: notes.length,
    nps: notes.length / duration,
    simultaneousRate: notes.length ? simultaneous / notes.length : 0,
    leftRate: smallNotes.length ? smallNotes.filter((note) => leftIds.has(note.lane_id)).length / smallNotes.length : 0.5,
    rimRate: notes.length ? notes.filter((note) => rimIds.has(note.lane_id)).length / notes.length : 0,
    minIntervalMs: intervals.length ? Math.min(...intervals) * 1000 : null,
    denseBar,
    laneCounts,
  }
}

export function bpmAtPulse(initialBpm: number, events: BpmEvent[], pulse: number): number {
  return events.filter((event) => event.pulse <= pulse).sort((a, b) => b.pulse - a.pulse)[0]?.bpm ?? initialBpm
}
