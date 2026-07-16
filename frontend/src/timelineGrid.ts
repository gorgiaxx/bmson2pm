import { gridPulseAt } from './timing'
import type { BarLine } from './types'

export type TimelineGridKind = 'bar' | 'beat' | 'subdivision'

export interface TimelineGridLine {
  pulse: number
  kind: TimelineGridKind
  label: string | null
}

interface TimelineGridOptions {
  visibleStart: number
  visibleEnd: number
  resolution: number
  divisor: number
  zoom: number
  barLines: BarLine[]
}

const MIN_SUBDIVISION_PIXELS = 5

export function buildTimelineGrid({
  visibleStart,
  visibleEnd,
  resolution,
  divisor,
  zoom,
  barLines,
}: TimelineGridOptions): TimelineGridLine[] {
  const start = Math.max(0, Math.min(visibleStart, visibleEnd))
  const end = Math.max(start, visibleStart, visibleEnd)
  const safeResolution = Math.max(1, resolution)
  const safeDivisor = Math.max(1, divisor)
  const explicitBars = explicitBarPulses(barLines)
  const bars = explicitBars.length
    ? extendExplicitBars(explicitBars, end, safeResolution)
    : fallbackBars(start, end, safeResolution)
  const barNumberByPulse = new Map(bars.map((bar) => [bar.pulse, bar.number]))
  const lines = new Map<number, TimelineGridLine>()
  const showSubdivisions = zoom / safeDivisor >= MIN_SUBDIVISION_PIXELS
  const firstGridIndex = Math.floor(start * safeDivisor / safeResolution)
  const lastGridIndex = Math.ceil(end * safeDivisor / safeResolution) + 1

  for (let gridIndex = firstGridIndex; gridIndex <= lastGridIndex; gridIndex += 1) {
    const pulse = gridPulseAt(gridIndex, safeResolution, safeDivisor)
    if (pulse < start || pulse > end || barNumberByPulse.has(pulse)) continue
    const isBeat = pulse % safeResolution === 0
    if (!isBeat && !showSubdivisions) continue
    lines.set(pulse, {
      pulse,
      kind: isBeat ? 'beat' : 'subdivision',
      label: isBeat ? beatLabelAt(pulse, safeResolution, bars) : null,
    })
  }

  for (const bar of bars) {
    if (bar.pulse < start || bar.pulse > end) continue
    lines.set(bar.pulse, {
      pulse: bar.pulse,
      kind: 'bar',
      label: String(bar.number).padStart(2, '0'),
    })
  }

  return [...lines.values()].sort((left, right) => left.pulse - right.pulse)
}

export function barNumberAtPulse(
  pulse: number,
  resolution: number,
  barLines: BarLine[],
): number {
  const safePulse = Number.isFinite(pulse) ? Math.max(0, pulse) : 0
  const safeResolution = Math.max(1, resolution)
  const barLength = safeResolution * 4
  const explicitBars = explicitBarPulses(barLines)
  if (!explicitBars.length) return Math.floor(safePulse / barLength) + 1

  let low = 0
  let high = explicitBars.length
  while (low < high) {
    const middle = Math.floor((low + high) / 2)
    if (explicitBars[middle] <= safePulse) low = middle + 1
    else high = middle
  }
  const explicitIndex = Math.max(0, low - 1)
  if (explicitIndex < explicitBars.length - 1) return explicitIndex + 1
  return explicitBars.length + Math.floor(
    (safePulse - explicitBars[explicitIndex]) / barLength,
  )
}

function explicitBarPulses(barLines: BarLine[]): number[] {
  const pulses = [...new Set(
    barLines
      .map((line) => Math.round(line.pulse))
      .filter((pulse) => Number.isFinite(pulse) && pulse >= 0),
  )].sort((left, right) => left - right)
  if (pulses.length && pulses[0] !== 0) pulses.unshift(0)
  return pulses
}

function extendExplicitBars(pulses: number[], end: number, resolution: number) {
  const bars = pulses.map((pulse, index) => ({ pulse, number: index + 1 }))
  const finalExplicitPulse = pulses.at(-1) ?? 0
  const barLength = resolution * 4
  const tailCount = Math.max(0, Math.floor((end - finalExplicitPulse) / barLength))
  for (let offset = 1; offset <= tailCount; offset += 1) {
    bars.push({
      pulse: finalExplicitPulse + offset * barLength,
      number: pulses.length + offset,
    })
  }
  return bars
}

function fallbackBars(start: number, end: number, resolution: number) {
  const barLength = resolution * 4
  const first = Math.max(0, Math.floor(start / barLength))
  const last = Math.ceil(end / barLength) + 1
  return Array.from({ length: last - first + 1 }, (_, offset) => {
    const index = first + offset
    return { pulse: index * barLength, number: index + 1 }
  })
}

function beatLabelAt(
  pulse: number,
  resolution: number,
  bars: Array<{ pulse: number; number: number }>,
): string {
  let measureStart = Math.floor(pulse / (resolution * 4)) * resolution * 4
  for (const bar of bars) {
    if (bar.pulse > pulse) break
    measureStart = bar.pulse
  }
  return String(Math.max(1, Math.floor((pulse - measureStart) / resolution) + 1))
}
