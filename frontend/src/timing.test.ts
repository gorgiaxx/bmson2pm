import { describe, expect, it } from 'vitest'
import { createDemoProject } from './demo'
import { calculateStats, gridPulseAt, pulseToSeconds, secondsToPulse, snapPulse } from './timing'

describe('timing conversions', () => {
  it('round trips pulses across BPM events', () => {
    const project = createDemoProject('test')
    project.timing.bpm_events = [{ id: 'bpm-1', pulse: 960, bpm: 180 }]
    for (const pulse of [0, 240, 960, 1440, 4800]) {
      expect(secondsToPulse(project, pulseToSeconds(project, pulse))).toBe(pulse)
    }
  })

  it('holds the playhead at a STOP and resumes with the active BPM', () => {
    const project = createDemoProject('stop-test')
    project.timing.bpm_events = [{ id: 'bpm-1', pulse: 240, bpm: 240, extensions: {} }]
    project.timing.stop_events = [{ id: 'stop-1', pulse: 480, duration_pulses: 240, extensions: {} }]
    const stopStart = pulseToSeconds(project, 480)
    expect(stopStart).toBeCloseTo(0.71875)
    expect(secondsToPulse(project, stopStart + 0.12)).toBe(480)
    expect(pulseToSeconds(project, 720)).toBeCloseTo(1.21875)
    expect(secondsToPulse(project, pulseToSeconds(project, 720))).toBe(720)
  })

  it('keeps rational grid positions on integer pulses', () => {
    expect([0, 1, 2, 3].map((index) => gridPulseAt(index, 100, 3))).toEqual([0, 33, 67, 100])
    expect(Number.isInteger(snapPulse(52, 100, 3))).toBe(true)
    expect(snapPulse(52, 100, 3)).toBe(67)
  })

  it('calculates six-lane statistics', () => {
    const project = createDemoProject('test')
    const chart = project.difficulties.hard
    const stats = calculateStats(project, chart.notes)
    expect(stats.total).toBe(chart.notes.length)
    expect(Object.keys(stats.laneCounts)).toHaveLength(6)
    expect(stats.leftRate).toBeGreaterThan(0)
    expect(stats.leftRate).toBeLessThan(1)
  })

  it('counts dedicated both-side inputs as simultaneous strikes', () => {
    const project = createDemoProject('simultaneous-inputs')
    const chart = project.difficulties.hard
    chart.notes = chart.notes.slice(0, 4).map((note, index) => ({
      ...note,
      pulse: index * 240,
      lane_id: [3, 4, 5, 6][index],
    }))
    const stats = calculateStats(project, chart.notes)
    expect(stats.simultaneousRate).toBe(0.5)
    expect(stats.leftRate).toBe(0.5)
  })

  it('keeps auxiliary events out of player statistics', () => {
    const project = createDemoProject('auxiliary-stats')
    const chart = project.difficulties.hard
    const source = chart.notes[0]
    project.lanes.push({
      id: 7, code: 'pm3_aux_8', display_name: 'PM3 Track 8', color: '#8aa1a8',
      hand: 'either', kind: 'auxiliary', default_key_sound_id: null, muted: false,
      extensions: { pm3: { track_id: 8 } },
    })
    const auxiliary = { ...source, id: 'auxiliary', lane_id: 7, playable: true }
    chart.notes.push(auxiliary)

    const inputNotes = chart.notes.filter((note) => project.lanes.find((lane) => lane.id === note.lane_id)?.kind === 'input')
    const stats = calculateStats(project, inputNotes)
    expect(stats.total).toBe(chart.notes.length - 1)
    expect(stats.laneCounts[7]).toBe(0)
  })
})
