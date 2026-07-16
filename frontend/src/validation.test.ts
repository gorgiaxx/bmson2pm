import { describe, expect, it } from 'vitest'
import { createDemoProject } from './demo'
import type { Note } from './types'
import { validateProjectLocally } from './validation'

function note(id: string, laneId: number, pulse: number, patch: Partial<Note> = {}): Note {
  return {
    id,
    lane_id: laneId,
    pulse,
    length: 0,
    key_sound_id: null,
    volume: 1,
    playable: true,
    continues: false,
    source: 'manual',
    notes: '',
    extensions: {},
    ...patch,
  }
}

describe('PM3 hand-limit validation', () => {
  it('warns when a both-hands Track activates with another Track', () => {
    const project = createDemoProject('both-hands-validation')
    project.difficulties.hard.notes = [
      note('both-rim', 3, 240),
      note('small-left', 1, 240),
    ]

    const issues = validateProjectLocally(project, 'hard')
    expect(issues).toEqual(expect.arrayContaining([
      expect.objectContaining({
        severity: 'warning',
        code: 'playability.both_hands_conflict',
        pulse: 240,
      }),
    ]))
    expect(issues.find((issue) => issue.code === 'playability.both_hands_conflict')?.message)
      .toContain('左小鼓')
  })

  it('counts distinct Track activations and warns when more than two are active', () => {
    const project = createDemoProject('three-track-validation')
    project.difficulties.hard.notes = [
      note('small-left-a', 1, 480),
      note('small-left-layer', 1, 480),
      note('small-right', 2, 480),
      note('rim-single', 4, 480),
    ]

    const issue = validateProjectLocally(project, 'hard')
      .find((candidate) => candidate.code === 'playability.too_many_simultaneous')
    expect(issue).toMatchObject({ severity: 'warning', pulse: 480 })
    expect(issue?.message).toContain('3 个 Track')
  })

  it('does not count Hold duration or non-playable notes as extra hands', () => {
    const project = createDemoProject('activation-only-validation')
    project.difficulties.hard.notes = [
      note('both-head-hold', 5, 0, { length: 480 }),
      note('inside-hold', 1, 240),
      note('non-playable', 2, 240, { playable: false }),
    ]

    const concurrencyCodes = new Set([
      'playability.both_hands_conflict',
      'playability.too_many_simultaneous',
    ])
    expect(validateProjectLocally(project, 'hard').some(
      (issue) => concurrencyCodes.has(issue.code),
    )).toBe(false)
  })
})
