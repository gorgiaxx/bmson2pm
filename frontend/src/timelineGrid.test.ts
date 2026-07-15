import { describe, expect, it } from 'vitest'
import { buildTimelineGrid } from './timelineGrid'

describe('buildTimelineGrid', () => {
  it('builds bar, beat, and subdivision lines on an integer grid', () => {
    const lines = buildTimelineGrid({
      visibleStart: 0,
      visibleEnd: 1000,
      resolution: 240,
      divisor: 4,
      zoom: 88,
      barLines: [],
    })
    expect(lines.find((line) => line.pulse === 0)).toEqual({ pulse: 0, kind: 'bar', label: '01' })
    expect(lines.find((line) => line.pulse === 60)).toEqual({ pulse: 60, kind: 'subdivision', label: null })
    expect(lines.find((line) => line.pulse === 240)).toEqual({ pulse: 240, kind: 'beat', label: '2' })
    expect(lines.find((line) => line.pulse === 960)).toEqual({ pulse: 960, kind: 'bar', label: '02' })
  })

  it('uses imported variable-measure bar lines instead of assuming 4/4', () => {
    const lines = buildTimelineGrid({
      visibleStart: 900,
      visibleEnd: 1500,
      resolution: 240,
      divisor: 4,
      zoom: 88,
      barLines: [
        { id: 'bar-1', pulse: 0 },
        { id: 'bar-2', pulse: 960 },
        { id: 'bar-3', pulse: 1440 },
      ],
    })
    expect(lines.find((line) => line.pulse === 960)).toEqual({ pulse: 960, kind: 'bar', label: '02' })
    expect(lines.find((line) => line.pulse === 1200)).toEqual({ pulse: 1200, kind: 'beat', label: '2' })
    expect(lines.find((line) => line.pulse === 1440)).toEqual({ pulse: 1440, kind: 'bar', label: '03' })
  })

  it('keeps beats and bars but hides overly dense subdivisions at low zoom', () => {
    const lines = buildTimelineGrid({
      visibleStart: 0,
      visibleEnd: 960,
      resolution: 240,
      divisor: 8,
      zoom: 28,
      barLines: [],
    })
    expect(lines.some((line) => line.kind === 'subdivision')).toBe(false)
    expect(lines.some((line) => line.kind === 'beat')).toBe(true)
    expect(lines.some((line) => line.kind === 'bar')).toBe(true)
  })

  it('preserves rational triplet positions without float accumulation', () => {
    const lines = buildTimelineGrid({
      visibleStart: 0,
      visibleEnd: 100,
      resolution: 100,
      divisor: 3,
      zoom: 88,
      barLines: [],
    })
    expect(lines.map((line) => line.pulse)).toEqual([0, 33, 67, 100])
  })
})
