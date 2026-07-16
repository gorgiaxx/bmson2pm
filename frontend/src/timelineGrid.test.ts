import { describe, expect, it } from 'vitest'
import { barNumberAtPulse, buildTimelineGrid } from './timelineGrid'

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

  it('continues four-beat bars after the final imported bar line', () => {
    const lines = buildTimelineGrid({
      visibleStart: 1400,
      visibleEnd: 3400,
      resolution: 240,
      divisor: 4,
      zoom: 88,
      barLines: [
        { id: 'bar-1', pulse: 0 },
        { id: 'bar-2', pulse: 960 },
        { id: 'bar-3', pulse: 1440 },
      ],
    })
    expect(lines.find((line) => line.pulse === 2400)).toEqual({ pulse: 2400, kind: 'bar', label: '04' })
    expect(lines.find((line) => line.pulse === 3360)).toEqual({ pulse: 3360, kind: 'bar', label: '05' })
  })

  it('reports visible bar numbers from variable measures and the supplemented tail', () => {
    const barLines = [
      { id: 'bar-1', pulse: 0 },
      { id: 'bar-2', pulse: 960 },
      { id: 'bar-3', pulse: 1440 },
    ]
    expect(barNumberAtPulse(1000, 240, barLines)).toBe(2)
    expect(barNumberAtPulse(1440, 240, barLines)).toBe(3)
    expect(barNumberAtPulse(2399, 240, barLines)).toBe(3)
    expect(barNumberAtPulse(2400, 240, barLines)).toBe(4)
    expect(barNumberAtPulse(3360, 240, barLines)).toBe(5)
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
