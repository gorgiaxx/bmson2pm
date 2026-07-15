import { beforeEach, describe, expect, it } from 'vitest'
import { createDemoProject } from './demo'
import { useEditorStore } from './store'

describe('incremental editor history', () => {
  beforeEach(() => {
    const project = createDemoProject('history-test')
    project.difficulties.hard.notes = project.difficulties.hard.notes.slice(0, 3)
    useEditorStore.getState().setProject(project)
  })

  it('undoes and redoes a note delta', () => {
    const originalCount = useEditorStore.getState().project.difficulties.hard.notes.length
    useEditorStore.getState().addNote(1, 9999)
    expect(useEditorStore.getState().project.difficulties.hard.notes).toHaveLength(originalCount + 1)
    expect(useEditorStore.getState().undoStack).toHaveLength(1)

    useEditorStore.getState().undo()
    expect(useEditorStore.getState().project.difficulties.hard.notes).toHaveLength(originalCount)
    useEditorStore.getState().redo()
    expect(useEditorStore.getState().project.difficulties.hard.notes).toHaveLength(originalCount + 1)
  })

  it('previews, cancels and commits a drag as one command', () => {
    const note = useEditorStore.getState().project.difficulties.hard.notes[0]
    const originalPulse = note.pulse
    useEditorStore.getState().beginNoteTransaction([note.id])
    useEditorStore.getState().previewMoveTransaction(240, 1)
    expect(useEditorStore.getState().project.difficulties.hard.notes.find((item) => item.id === note.id)?.pulse).toBe(originalPulse + 240)
    expect(useEditorStore.getState().undoStack).toHaveLength(0)

    useEditorStore.getState().cancelNoteTransaction()
    expect(useEditorStore.getState().project.difficulties.hard.notes.find((item) => item.id === note.id)?.pulse).toBe(originalPulse)

    useEditorStore.getState().beginNoteTransaction([note.id])
    useEditorStore.getState().previewMoveTransaction(240, 1)
    useEditorStore.getState().finishNoteTransaction()
    expect(useEditorStore.getState().undoStack).toHaveLength(1)
    useEditorStore.getState().undo()
    expect(useEditorStore.getState().project.difficulties.hard.notes.find((item) => item.id === note.id)?.pulse).toBe(originalPulse)
  })

  it('resizes a long note transactionally', () => {
    const note = useEditorStore.getState().project.difficulties.hard.notes[0]
    useEditorStore.getState().beginNoteTransaction([note.id])
    useEditorStore.getState().previewResizeTransaction(note.id, 360)
    useEditorStore.getState().finishNoteTransaction()
    expect(useEditorStore.getState().project.difficulties.hard.notes.find((item) => item.id === note.id)?.length).toBe(360)
    useEditorStore.getState().undo()
    expect(useEditorStore.getState().project.difficulties.hard.notes.find((item) => item.id === note.id)?.length).toBe(note.length)
  })

  it('mirrors only the left and right small-drum lanes', () => {
    const state = useEditorStore.getState()
    const project = createDemoProject('mirror-semantics')
    const source = project.difficulties.hard.notes.slice(0, 3).map((note, index) => ({
      ...note,
      id: `mirror-${index}`,
      lane_id: [1, 3, 5][index],
    }))
    project.difficulties.hard.notes = source
    state.setProject(project)
    useEditorStore.getState().selectMany(source.map((note) => note.id))
    useEditorStore.getState().mirrorSelected()
    expect(useEditorStore.getState().project.difficulties.hard.notes.map((note) => note.lane_id)).toEqual([2, 3, 5])
  })

  it('creates an anonymous track, migrates a tick range, and restores it through history', () => {
    const project = createDemoProject('anonymous-move')
    const base = project.difficulties.hard.notes[0]
    project.difficulties.hard.notes = [
      { ...base, id: 'move-a', lane_id: 1, pulse: 100 },
      { ...base, id: 'move-b', lane_id: 1, pulse: 300 },
      { ...base, id: 'move-c', lane_id: 2, pulse: 100 },
    ]
    useEditorStore.getState().setProject(project)

    const laneId = useEditorStore.getState().createAnonymousLane(1, { start: 0, end: 200 })
    let state = useEditorStore.getState()
    expect(laneId).toBe(7)
    expect(state.project.lanes.at(-1)).toMatchObject({ id: 7, kind: 'anonymous' })
    expect(state.project.difficulties.hard.notes.find((note) => note.id === 'move-a')?.lane_id).toBe(7)
    expect(state.project.difficulties.hard.notes.find((note) => note.id === 'move-b')?.lane_id).toBe(1)

    state.undo()
    state = useEditorStore.getState()
    expect(state.project.lanes).toHaveLength(6)
    expect(state.project.difficulties.hard.notes.find((note) => note.id === 'move-a')?.lane_id).toBe(1)
    state.redo()
    expect(useEditorStore.getState().project.lanes).toHaveLength(7)
  })

  it('moves and swaps whole tracks with optional tick ranges', () => {
    const project = createDemoProject('track-switch')
    const base = project.difficulties.hard.notes[0]
    project.difficulties.hard.notes = [
      { ...base, id: 'switch-a', lane_id: 1, pulse: 100 },
      { ...base, id: 'switch-b', lane_id: 1, pulse: 400 },
      { ...base, id: 'switch-c', lane_id: 2, pulse: 100 },
    ]
    useEditorStore.getState().setProject(project)

    useEditorStore.getState().swapLaneNotes(1, 2, { start: 0, end: 200 })
    expect(useEditorStore.getState().project.difficulties.hard.notes.map((note) => note.lane_id)).toEqual([2, 1, 1])
    useEditorStore.getState().moveLaneNotes(1, 3)
    expect(useEditorStore.getState().project.difficulties.hard.notes.map((note) => note.lane_id)).toEqual([2, 3, 3])
    useEditorStore.getState().undo()
    expect(useEditorStore.getState().project.difficulties.hard.notes.map((note) => note.lane_id)).toEqual([2, 1, 1])
  })

  it('merges conflicts to one target note and removes an emptied anonymous track', () => {
    const project = createDemoProject('track-merge')
    project.lanes.push({
      id: 7, code: 'bms_19', display_name: '匿名 Track 19', color: '#8aa1a8', hand: 'either',
      kind: 'anonymous', default_key_sound_id: null, muted: false, extensions: { bms: { channel: '19' } },
    })
    project.game_specific_data.bms_lane_map = { '11': 1, '19': 7 }
    project.game_specific_data.notelist_track_map = { '0': 1, '19': 7 }
    const base = project.difficulties.hard.notes[0]
    project.difficulties.hard.notes = [
      { ...base, id: 'target-wins', lane_id: 1, pulse: 100 },
      { ...base, id: 'source-conflict', lane_id: 7, pulse: 100 },
      { ...base, id: 'source-only', lane_id: 7, pulse: 200 },
    ]
    useEditorStore.getState().setProject(project)

    useEditorStore.getState().mergeLaneInto(7, 1)
    let state = useEditorStore.getState()
    expect(state.project.difficulties.hard.notes.map((note) => [note.id, note.lane_id])).toEqual([
      ['target-wins', 1], ['source-only', 1],
    ])
    expect(state.project.lanes.some((lane) => lane.id === 7)).toBe(false)
    expect(state.project.game_specific_data.bms_lane_map).toEqual({ '11': 1 })
    expect(state.project.game_specific_data.notelist_track_map).toEqual({ '0': 1 })

    state.undo()
    state = useEditorStore.getState()
    expect(state.project.lanes.some((lane) => lane.id === 7)).toBe(true)
    expect(state.project.difficulties.hard.notes).toHaveLength(3)
  })

  it('uses Ctrl+A semantics to select editor notes rather than page text', () => {
    const state = useEditorStore.getState()
    state.selectAllNotes()
    expect(useEditorStore.getState().selectedIds.size).toBe(state.project.difficulties.hard.notes.length)
  })

  it('changes a track color and restores it through history', () => {
    const original = useEditorStore.getState().project.lanes[0].color
    useEditorStore.getState().setLaneColor(1, '#ef5350')
    expect(useEditorStore.getState().project.lanes[0].color).toBe('#ef5350')

    useEditorStore.getState().undo()
    expect(useEditorStore.getState().project.lanes[0].color).toBe(original)
    useEditorStore.getState().redo()
    expect(useEditorStore.getState().project.lanes[0].color).toBe('#ef5350')
  })

  it('classifies an anonymous track as a non-scoring PM3 auxiliary track', () => {
    const project = createDemoProject('pm3-auxiliary-classification')
    project.lanes.push({
      id: 7, code: 'notelist_21', display_name: 'NoteList Track 21', color: '#8aa1a8',
      hand: 'either', kind: 'anonymous', default_key_sound_id: null, muted: false,
      extensions: { notelist: { track_id: 21 } },
    })
    const source = project.difficulties.hard.notes[0]
    project.difficulties.hard.notes.push({ ...source, id: 'aux-candidate', lane_id: 7, playable: true })
    useEditorStore.getState().setProject(project)

    useEditorStore.getState().setLanePm3Track(7, 14)
    let state = useEditorStore.getState()
    expect(state.project.lanes.at(-1)).toMatchObject({ kind: 'auxiliary', extensions: { pm3: { track_id: 14 } } })
    expect(state.project.difficulties.hard.notes.find((note) => note.id === 'aux-candidate')?.playable).toBe(false)

    state.undo()
    state = useEditorStore.getState()
    expect(state.project.lanes.at(-1)?.kind).toBe('anonymous')
    expect(state.project.difficulties.hard.notes.find((note) => note.id === 'aux-candidate')?.playable).toBe(true)
  })
})
