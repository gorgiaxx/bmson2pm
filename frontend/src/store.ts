import { create } from 'zustand'
import { createDemoProject } from './demo'
import { migrateLaneSemantics } from './constants'
import { gridPulseAt, snapPulse } from './timing'
import type { DifficultyChart, DifficultyId, EditorTool, KeySoundAsset, Lane, Note, SongProject } from './types'

type SaveStatus = 'saved' | 'dirty' | 'saving' | 'error'
type ProjectTransform = (project: SongProject) => SongProject
type EditableProjectSection = 'metadata' | 'timing'

interface HistoryEntry {
  key: string
  label: string
  timestamp: number
  merge: boolean
  undo: ProjectTransform
  redo: ProjectTransform
}

interface NoteSnapshot {
  id: string
  index: number
  note: Note | null
}

interface NoteTransaction {
  difficulty: DifficultyId
  before: NoteSnapshot[]
  after: NoteSnapshot[]
}

export interface TickRange {
  start: number
  end: number
}

interface EditorState {
  project: SongProject
  activeDifficulty: DifficultyId
  referenceDifficulty: DifficultyId | null
  activeLaneId: number | null
  selectedIds: Set<string>
  tool: EditorTool
  quantizeDivisor: number
  zoom: number
  scrollPulse: number
  playheadSeconds: number
  saveStatus: SaveStatus
  saveMessage: string
  undoStack: HistoryEntry[]
  redoStack: HistoryEntry[]
  clipboard: Note[]
  editTransaction: NoteTransaction | null
  setProject: (project: SongProject, clearHistory?: boolean) => void
  setActiveDifficulty: (difficulty: DifficultyId) => void
  setReferenceDifficulty: (difficulty: DifficultyId | null) => void
  setActiveLane: (laneId: number | null) => void
  setTool: (tool: EditorTool) => void
  setQuantizeDivisor: (divisor: number) => void
  setZoom: (zoom: number) => void
  setScrollPulse: (pulse: number) => void
  setPlayheadSeconds: (seconds: number) => void
  setSaveStatus: (status: SaveStatus, message?: string) => void
  selectOnly: (id: string | null) => void
  toggleSelection: (id: string) => void
  selectMany: (ids: string[], append?: boolean) => void
  selectAllNotes: () => void
  selectLaneNotes: (laneId: number, range?: TickRange | null) => void
  addNote: (laneId: number, pulse: number) => void
  deleteSelected: () => void
  moveNotes: (ids: string[], deltaPulse: number, laneDelta: number) => void
  updateSelected: (patch: Partial<Note>) => void
  copySelected: () => void
  pasteAt: (pulse: number) => void
  duplicateSelected: () => void
  mirrorSelected: () => void
  quantizeSelected: () => void
  updateMetadata: (patch: Partial<SongProject['metadata']>) => void
  updateTiming: (patch: Partial<SongProject['timing']>) => void
  updateDifficulty: (difficulty: DifficultyId, patch: Partial<DifficultyChart>) => void
  copyDifficulty: (from: DifficultyId, to: DifficultyId) => void
  addKeySound: (asset: KeySoundAsset) => void
  updateKeySound: (assetId: string, patch: Partial<Pick<KeySoundAsset, 'name' | 'volume' | 'delay_ms' | 'tags'>>) => void
  removeKeySound: (assetId: string) => boolean
  setLaneDefaultKeySound: (laneId: number, assetId: string | null) => void
  toggleLaneMute: (laneId: number) => void
  setLaneColor: (laneId: number, color: string) => void
  setLanePm3Track: (laneId: number, trackId: number | null) => void
  createAnonymousLane: (sourceLaneId?: number, range?: TickRange | null) => number | null
  moveLaneNotes: (sourceLaneId: number, targetLaneId: number, range?: TickRange | null) => void
  swapLaneNotes: (sourceLaneId: number, targetLaneId: number, range?: TickRange | null) => void
  mergeLaneInto: (sourceLaneId: number, targetLaneId: number, range?: TickRange | null) => void
  removeEmptyAnonymousLane: (laneId: number) => void
  beginNoteTransaction: (ids: string[]) => void
  previewMoveTransaction: (deltaPulse: number, laneDelta: number) => void
  previewResizeTransaction: (noteId: string, length: number) => void
  finishNoteTransaction: () => void
  cancelNoteTransaction: () => void
  undo: () => void
  redo: () => void
}

const uid = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`
const clone = <T,>(value: T): T => structuredClone(value)
const touched = (project: SongProject): SongProject => ({ ...project, updated_at: new Date().toISOString() })

const LEGACY_INPUT_LANE_MAP: Record<number, number> = {
  1: 5, 2: 4, 3: 3, 4: 2, 5: 1, 6: 6,
}

function migrateLegacyInputLaneIds(project: SongProject): void {
  const lanes = new Map(project.lanes.map((lane) => [lane.id, lane]))
  const oldCodes: Record<number, Set<string>> = {
    1: new Set(['small_left']),
    2: new Set(['small_right']),
    3: new Set(['rim_simultaneous', 'rim_left']),
    4: new Set(['rim_single', 'rim_right']),
    5: new Set(['head_simultaneous', 'head_left']),
    6: new Set(['head_single', 'head_right']),
  }
  const legacy = Object.entries(oldCodes).every(([rawId, codes]) => {
    const lane = lanes.get(Number(rawId))
    return lane !== undefined && codes.has(lane.code)
  })
  if (!legacy) return

  project.lanes = project.lanes
    .map((lane) => ({ ...lane, id: LEGACY_INPUT_LANE_MAP[lane.id] ?? lane.id }))
    .sort((left, right) => left.id - right.id)
  for (const chart of Object.values(project.difficulties)) {
    chart.notes = chart.notes.map((note) => ({
      ...note,
      lane_id: LEGACY_INPUT_LANE_MAP[note.lane_id] ?? note.lane_id,
    }))
  }
  project.key_sounds = project.key_sounds.map((asset) => ({
    ...asset,
    lane_ids: asset.lane_ids.map((laneId) => LEGACY_INPUT_LANE_MAP[laneId] ?? laneId),
  }))
  for (const key of ['bms_lane_map', 'notelist_track_map', 'pm3_track_lane_map']) {
    const mapping = project.game_specific_data[key]
    if (!mapping || typeof mapping !== 'object' || Array.isArray(mapping)) continue
    project.game_specific_data[key] = Object.fromEntries(
      Object.entries(mapping).map(([source, target]) => {
        const laneId = Number(target)
        return [source, Number.isInteger(laneId) ? LEGACY_INPUT_LANE_MAP[laneId] ?? laneId : target]
      }),
    )
  }
}

function normalizeProject(project: SongProject): SongProject {
  const next = clone(project)
  next.schema_version = '1.3'
  migrateLegacyInputLaneIds(next)
  next.lanes = migrateLaneSemantics(next.lanes)
  next.game_specific_data.lane_semantics = 'pm3-six-input-v3'
  next.timing.bar_lines = (next.timing.bar_lines ?? []).map((line, index) => (
    typeof line === 'number'
      ? { id: `legacy-bar-${index}-${line}`, pulse: line, extensions: {} }
      : { ...line, extensions: line.extensions ?? {} }
  ))
  next.timing.bpm_events = next.timing.bpm_events.map((event) => ({ ...event, extensions: event.extensions ?? {} }))
  next.timing.stop_events = next.timing.stop_events.map((event) => ({ ...event, extensions: event.extensions ?? {} }))
  for (const chart of Object.values(next.difficulties)) {
    chart.extensions = chart.extensions ?? {}
    chart.notes = chart.notes.map((note) => ({
      ...note,
      continues: note.continues ?? false,
      extensions: note.extensions ?? {},
    }))
  }
  next.key_sounds = next.key_sounds.map((asset) => ({
    ...asset,
    source: asset.source ?? 'manual',
    extensions: asset.extensions ?? {},
  }))
  return next
}

function normalizedRange(range?: TickRange | null): TickRange | null {
  if (!range) return null
  return {
    start: Math.max(0, Math.min(Math.round(range.start), Math.round(range.end))),
    end: Math.max(0, Math.max(Math.round(range.start), Math.round(range.end))),
  }
}

function inRange(pulse: number, range?: TickRange | null): boolean {
  const normalized = normalizedRange(range)
  return !normalized || (pulse >= normalized.start && pulse <= normalized.end)
}

function projectHistory(key: string, label: string, before: SongProject, after: SongProject): HistoryEntry {
  const previous = clone(before)
  const next = clone(after)
  return {
    key,
    label,
    timestamp: Date.now(),
    merge: false,
    undo: () => clone(previous),
    redo: () => clone(next),
  }
}

function removeLaneMappings(project: SongProject, laneId: number): SongProject {
  const gameSpecificData = { ...project.game_specific_data }
  for (const key of ['bms_lane_map', 'notelist_track_map']) {
    const stored = gameSpecificData[key]
    if (!stored || typeof stored !== 'object' || Array.isArray(stored)) continue
    gameSpecificData[key] = Object.fromEntries(
      Object.entries(stored).filter(([, mappedLane]) => Number(mappedLane) !== laneId),
    )
  }
  return {
    ...project,
    game_specific_data: gameSpecificData,
  }
}

function noteSnapshots(notes: Note[], ids: Set<string>): NoteSnapshot[] {
  const snapshots: NoteSnapshot[] = []
  notes.forEach((note, index) => {
    if (ids.has(note.id)) snapshots.push({ id: note.id, index, note })
  })
  return snapshots
}

function applyNoteSnapshots(
  project: SongProject,
  difficulty: DifficultyId,
  snapshots: NoteSnapshot[],
): SongProject {
  const chart = project.difficulties[difficulty]
  const affected = new Set(snapshots.map((snapshot) => snapshot.id))
  const notes = chart.notes.filter((note) => !affected.has(note.id))
  for (const snapshot of [...snapshots].filter((item) => item.note).sort((a, b) => a.index - b.index)) {
    notes.splice(Math.min(Math.max(snapshot.index, 0), notes.length), 0, snapshot.note as Note)
  }
  return {
    ...project,
    difficulties: {
      ...project.difficulties,
      [difficulty]: { ...chart, notes },
    },
  }
}

function noteHistory(
  key: string,
  label: string,
  difficulty: DifficultyId,
  before: NoteSnapshot[],
  after: NoteSnapshot[],
  merge = false,
): HistoryEntry {
  return {
    key,
    label,
    timestamp: Date.now(),
    merge,
    undo: (project) => applyNoteSnapshots(project, difficulty, before),
    redo: (project) => applyNoteSnapshots(project, difficulty, after),
  }
}

function sameSnapshots(left: NoteSnapshot[], right: NoteSnapshot[]): boolean {
  return JSON.stringify(left.map((item) => item.note)) === JSON.stringify(right.map((item) => item.note))
}

export const useEditorStore = create<EditorState>((set, get) => {
  const record = (
    nextProject: SongProject,
    entry: HistoryEntry,
    selection = get().selectedIds,
    extra: Partial<EditorState> = {},
  ) => {
    const state = get()
    const previous = state.undoStack.at(-1)
    const shouldMerge = entry.merge
      && previous?.merge
      && previous.key === entry.key
      && entry.timestamp - previous.timestamp < 750
    const storedEntry = shouldMerge
      ? { ...entry, undo: previous.undo }
      : entry
    const undoStack = shouldMerge
      ? [...state.undoStack.slice(0, -1), storedEntry]
      : [...state.undoStack.slice(-49), storedEntry]
    set({
      project: touched(nextProject),
      selectedIds: selection,
      saveStatus: 'dirty',
      saveMessage: '',
      undoStack,
      redoStack: [],
      ...extra,
    })
  }

  const recordObjectPatch = <K extends EditableProjectSection>(
    section: K,
    patch: Partial<SongProject[K]>,
    key: string,
    label: string,
  ) => {
    const current = get().project
    const currentSection = current[section]
    const before: Partial<SongProject[K]> = {}
    const after: Partial<SongProject[K]> = {}
    for (const rawKey of Object.keys(patch) as Array<keyof SongProject[K]>) {
      if (Object.is(currentSection[rawKey], patch[rawKey])) continue
      before[rawKey] = currentSection[rawKey]
      after[rawKey] = patch[rawKey]
    }
    if (!Object.keys(after).length) return
    const apply = (project: SongProject, values: Partial<SongProject[K]>): SongProject => ({
      ...project,
      [section]: { ...project[section], ...values },
    })
    record(
      apply(current, after),
      {
        key,
        label,
        timestamp: Date.now(),
        merge: true,
        undo: (project) => apply(project, before),
        redo: (project) => apply(project, after),
      },
    )
  }

  return {
    project: createDemoProject(),
    activeDifficulty: 'hard',
    referenceDifficulty: null,
    activeLaneId: 1,
    selectedIds: new Set(),
    tool: 'select',
    quantizeDivisor: 4,
    zoom: 88,
    scrollPulse: 0,
    playheadSeconds: 0,
    saveStatus: 'saved',
    saveMessage: '本地示例',
    undoStack: [],
    redoStack: [],
    clipboard: [],
    editTransaction: null,

    setProject: (project, clearHistory = true) => set({
      project: normalizeProject(project),
      activeLaneId: project.lanes[0]?.id ?? null,
      selectedIds: new Set(),
      saveStatus: 'saved',
      saveMessage: '',
      editTransaction: null,
      ...(clearHistory ? { undoStack: [], redoStack: [] } : {}),
    }),
    setActiveDifficulty: (activeDifficulty) => {
      const state = get()
      const project = state.editTransaction
        ? applyNoteSnapshots(state.project, state.editTransaction.difficulty, state.editTransaction.before)
        : state.project
      set({ project, activeDifficulty, selectedIds: new Set(), editTransaction: null })
    },
    setReferenceDifficulty: (referenceDifficulty) => set({ referenceDifficulty }),
    setActiveLane: (activeLaneId) => set((state) => ({
      activeLaneId: activeLaneId !== null && state.project.lanes.some((lane) => lane.id === activeLaneId)
        ? activeLaneId
        : null,
    })),
    setTool: (tool) => set({ tool }),
    setQuantizeDivisor: (quantizeDivisor) => set({ quantizeDivisor: Math.max(1, Math.round(quantizeDivisor)) }),
    setZoom: (zoom) => set({ zoom: Math.min(260, Math.max(28, zoom)) }),
    setScrollPulse: (scrollPulse) => set({ scrollPulse: Math.max(0, scrollPulse) }),
    setPlayheadSeconds: (playheadSeconds) => set({ playheadSeconds: Math.max(0, playheadSeconds) }),
    setSaveStatus: (saveStatus, saveMessage = '') => set({ saveStatus, saveMessage }),

    selectOnly: (id) => set({ selectedIds: id ? new Set([id]) : new Set() }),
    toggleSelection: (id) => set((state) => {
      const next = new Set(state.selectedIds)
      next.has(id) ? next.delete(id) : next.add(id)
      return { selectedIds: next }
    }),
    selectMany: (ids, append = false) => set((state) => ({
      selectedIds: new Set(append ? [...state.selectedIds, ...ids] : ids),
    })),
    selectAllNotes: () => set((state) => ({
      selectedIds: new Set(state.project.difficulties[state.activeDifficulty].notes.map((note) => note.id)),
    })),
    selectLaneNotes: (laneId, range = null) => set((state) => ({
      activeLaneId: laneId,
      selectedIds: new Set(
        state.project.difficulties[state.activeDifficulty].notes
          .filter((note) => note.lane_id === laneId && inRange(note.pulse, range))
          .map((note) => note.id),
      ),
    })),

    addNote: (laneId, pulse) => {
      const state = get()
      const chart = state.project.difficulties[state.activeDifficulty]
      if (chart.locked) return
      const snapped = snapPulse(pulse, state.project.timing.resolution, state.quantizeDivisor)
      const existing = chart.notes.find((note) => note.lane_id === laneId && note.pulse === snapped)
      if (existing) {
        set({ selectedIds: new Set([existing.id]) })
        return
      }
      const note: Note = {
        id: uid(), lane_id: laneId, pulse: snapped, length: 0, key_sound_id: null,
        volume: 1,
        playable: state.project.lanes.find((lane) => lane.id === laneId)?.kind === 'input',
        continues: false, source: 'manual', notes: '', extensions: {},
      }
      const after = [{ id: note.id, index: chart.notes.length, note }]
      record(
        applyNoteSnapshots(state.project, state.activeDifficulty, after),
        noteHistory('notes:add', '添加音符', state.activeDifficulty, [{ ...after[0], note: null }], after),
        new Set([note.id]),
      )
    },

    deleteSelected: () => {
      const state = get()
      if (!state.selectedIds.size || state.project.difficulties[state.activeDifficulty].locked) return
      const before = noteSnapshots(state.project.difficulties[state.activeDifficulty].notes, state.selectedIds)
      const after = before.map((item) => ({ ...item, note: null }))
      record(
        applyNoteSnapshots(state.project, state.activeDifficulty, after),
        noteHistory('notes:delete', '删除音符', state.activeDifficulty, before, after),
        new Set(),
      )
    },

    moveNotes: (ids, deltaPulse, laneDelta) => {
      const state = get()
      if ((!deltaPulse && !laneDelta) || state.project.difficulties[state.activeDifficulty].locked) return
      const before = noteSnapshots(state.project.difficulties[state.activeDifficulty].notes, new Set(ids))
      const laneIds = state.project.lanes.map((lane) => lane.id)
      const after = before.map((snapshot) => {
        const note = snapshot.note as Note
        const laneIndex = laneIds.indexOf(note.lane_id)
        const targetLaneId = laneIds[Math.min(laneIds.length - 1, Math.max(0, laneIndex + laneDelta))]
        return {
          ...snapshot,
          note: {
            ...note,
            pulse: Math.max(0, Math.round(note.pulse + deltaPulse)),
            lane_id: targetLaneId,
            playable: state.project.lanes.find((lane) => lane.id === targetLaneId)?.kind === 'input',
          },
        }
      })
      record(
        applyNoteSnapshots(state.project, state.activeDifficulty, after),
        noteHistory('notes:move', '移动音符', state.activeDifficulty, before, after),
      )
    },

    updateSelected: (patch) => {
      const state = get()
      if (!state.selectedIds.size || state.project.difficulties[state.activeDifficulty].locked) return
      const before = noteSnapshots(state.project.difficulties[state.activeDifficulty].notes, state.selectedIds)
      const after = before.map((snapshot) => ({
        ...snapshot,
        note: { ...(snapshot.note as Note), ...patch },
      }))
      if (sameSnapshots(before, after)) return
      const key = `notes:update:${state.activeDifficulty}:${[...state.selectedIds].sort().join(',')}:${Object.keys(patch).sort().join(',')}`
      record(
        applyNoteSnapshots(state.project, state.activeDifficulty, after),
        noteHistory(key, '修改音符', state.activeDifficulty, before, after, true),
      )
    },

    copySelected: () => {
      const state = get()
      const notes = state.project.difficulties[state.activeDifficulty].notes.filter((note) => state.selectedIds.has(note.id))
      set({ clipboard: clone(notes) })
    },

    pasteAt: (pulse) => {
      const state = get()
      if (!state.clipboard.length || state.project.difficulties[state.activeDifficulty].locked) return
      const chart = state.project.difficulties[state.activeDifficulty]
      const anchor = snapPulse(pulse, state.project.timing.resolution, state.quantizeDivisor)
      const first = Math.min(...state.clipboard.map((note) => note.pulse))
      const pasted = state.clipboard.map((note) => ({
        ...clone(note), id: uid(), pulse: Math.max(0, anchor + note.pulse - first), source: 'copied',
      }))
      const after = pasted.map((note, index) => ({ id: note.id, index: chart.notes.length + index, note }))
      record(
        applyNoteSnapshots(state.project, state.activeDifficulty, after),
        noteHistory('notes:paste', '粘贴音符', state.activeDifficulty, after.map((item) => ({ ...item, note: null })), after),
        new Set(pasted.map((note) => note.id)),
      )
    },

    duplicateSelected: () => {
      const state = get()
      const chart = state.project.difficulties[state.activeDifficulty]
      if (chart.locked) return
      const selected = chart.notes.filter((note) => state.selectedIds.has(note.id))
      if (!selected.length) return
      const step = gridPulseAt(1, state.project.timing.resolution, state.quantizeDivisor)
      const pasted = selected.map((note) => ({ ...clone(note), id: uid(), pulse: note.pulse + step, source: 'copied' }))
      const after = pasted.map((note, index) => ({ id: note.id, index: chart.notes.length + index, note }))
      record(
        applyNoteSnapshots(state.project, state.activeDifficulty, after),
        noteHistory('notes:duplicate', '重复音符', state.activeDifficulty, after.map((item) => ({ ...item, note: null })), after),
        new Set(pasted.map((note) => note.id)),
      )
    },

    mirrorSelected: () => {
      const state = get()
      const before = noteSnapshots(state.project.difficulties[state.activeDifficulty].notes, state.selectedIds)
      if (!before.length) return
      const left = state.project.lanes.find((lane) => lane.code === 'small_left')
      const right = state.project.lanes.find((lane) => lane.code === 'small_right')
      const laneMap: Record<number, number> = left && right
        ? { [left.id]: right.id, [right.id]: left.id }
        : {}
      const after = before.map((snapshot) => ({
        ...snapshot,
        note: { ...(snapshot.note as Note), lane_id: laneMap[(snapshot.note as Note).lane_id] ?? (snapshot.note as Note).lane_id },
      }))
      record(
        applyNoteSnapshots(state.project, state.activeDifficulty, after),
        noteHistory('notes:mirror', '镜像左右小鼓', state.activeDifficulty, before, after),
      )
    },

    quantizeSelected: () => {
      const state = get()
      const before = noteSnapshots(state.project.difficulties[state.activeDifficulty].notes, state.selectedIds)
      if (!before.length) return
      const after = before.map((snapshot) => ({
        ...snapshot,
        note: {
          ...(snapshot.note as Note),
          pulse: snapPulse((snapshot.note as Note).pulse, state.project.timing.resolution, state.quantizeDivisor),
        },
      }))
      if (sameSnapshots(before, after)) return
      record(
        applyNoteSnapshots(state.project, state.activeDifficulty, after),
        noteHistory('notes:quantize', '量化音符', state.activeDifficulty, before, after),
      )
    },

    updateMetadata: (patch) => recordObjectPatch('metadata', patch, `metadata:${Object.keys(patch).sort().join(',')}`, '修改歌曲信息'),
    updateTiming: (patch) => recordObjectPatch('timing', patch, `timing:${Object.keys(patch).sort().join(',')}`, '修改时间设置'),
    updateDifficulty: (difficulty, patch) => {
      const state = get()
      const before = state.project.difficulties[difficulty]
      const after = { ...before, ...patch }
      if (JSON.stringify(before) === JSON.stringify(after)) return
      const apply = (project: SongProject, chart: DifficultyChart): SongProject => ({
        ...project,
        difficulties: { ...project.difficulties, [difficulty]: chart },
      })
      record(
        apply(state.project, after),
        {
          key: `difficulty:${difficulty}:${Object.keys(patch).sort().join(',')}`,
          label: '修改难度',
          timestamp: Date.now(),
          merge: true,
          undo: (project) => apply(project, before),
          redo: (project) => apply(project, after),
        },
      )
    },
    copyDifficulty: (from, to) => {
      const state = get()
      const before = state.project.difficulties[to]
      const notes = state.project.difficulties[from].notes.map((note) => ({ ...clone(note), id: uid(), source: `copied:${from}` }))
      const after = { ...before, notes }
      const apply = (project: SongProject, chart: DifficultyChart): SongProject => ({
        ...project,
        difficulties: { ...project.difficulties, [to]: chart },
      })
      record(
        apply(state.project, after),
        {
          key: `difficulty:copy:${to}`,
          label: '复制难度',
          timestamp: Date.now(),
          merge: false,
          undo: (project) => apply(project, before),
          redo: (project) => apply(project, after),
        },
      )
    },
    addKeySound: (asset) => {
      const before = get().project
      if (before.key_sounds.some((item) => item.id === asset.id)) return
      const after = { ...before, key_sounds: [...before.key_sounds, clone(asset)] }
      record(
        after,
        projectHistory('key-sound:add', `添加 Key 音 ${asset.name}`, before, after),
      )
    },
    updateKeySound: (assetId, patch) => {
      const before = get().project
      const current = before.key_sounds.find((asset) => asset.id === assetId)
      if (!current) return
      const safePatch = {
        ...patch,
        ...(patch.volume === undefined ? {} : { volume: Math.max(0, Math.min(2, patch.volume)) }),
        ...(patch.delay_ms === undefined ? {} : { delay_ms: Number.isFinite(patch.delay_ms) ? patch.delay_ms : 0 }),
      }
      const updated = { ...current, ...safePatch }
      if (JSON.stringify(current) === JSON.stringify(updated)) return
      const after = {
        ...before,
        key_sounds: before.key_sounds.map((asset) => asset.id === assetId ? updated : asset),
      }
      record(
        after,
        {
          ...projectHistory(`key-sound:update:${assetId}`, `修改 Key 音 ${current.name}`, before, after),
          merge: true,
        },
      )
    },
    removeKeySound: (assetId) => {
      const before = get().project
      const asset = before.key_sounds.find((item) => item.id === assetId)
      if (!asset) return false
      const referencedByLane = before.lanes.some((lane) => lane.default_key_sound_id === assetId)
      const referencedByNote = Object.values(before.difficulties).some((chart) => (
        chart.notes.some((note) => note.key_sound_id === assetId)
      ))
      if (referencedByLane || referencedByNote) return false
      const after = {
        ...before,
        key_sounds: before.key_sounds.filter((item) => item.id !== assetId),
      }
      record(
        after,
        projectHistory(`key-sound:remove:${assetId}`, `删除 Key 音 ${asset.name}`, before, after),
      )
      return true
    },
    setLaneDefaultKeySound: (laneId, assetId) => {
      const before = get().project
      if (assetId !== null && !before.key_sounds.some((asset) => asset.id === assetId)) return
      const lane = before.lanes.find((item) => item.id === laneId)
      if (!lane || lane.default_key_sound_id === assetId) return
      const after = {
        ...before,
        lanes: before.lanes.map((item) => item.id === laneId
          ? { ...item, default_key_sound_id: assetId }
          : item),
      }
      record(
        after,
        projectHistory(
          `lane:key-sound:${laneId}`,
          assetId === null ? `清除 ${lane.display_name} 默认音色` : `设置 ${lane.display_name} 默认音色`,
          before,
          after,
        ),
      )
    },
    toggleLaneMute: (laneId) => {
      const state = get()
      const before = state.project.lanes
      const after = before.map((lane) => lane.id === laneId ? { ...lane, muted: !lane.muted } : lane)
      const apply = (project: SongProject, lanes: SongProject['lanes']): SongProject => ({ ...project, lanes })
      record(
        apply(state.project, after),
        {
          key: `lane:mute:${laneId}`,
          label: '切换轨道静音',
          timestamp: Date.now(),
          merge: false,
          undo: (project) => apply(project, before),
          redo: (project) => apply(project, after),
        },
      )
    },
    setLaneColor: (laneId, color) => {
      const state = get()
      const before = state.project.lanes
      const current = before.find((lane) => lane.id === laneId)
      if (!current || current.color.toLowerCase() === color.toLowerCase()) return
      const after = before.map((lane) => lane.id === laneId ? { ...lane, color } : lane)
      const apply = (project: SongProject, lanes: SongProject['lanes']): SongProject => ({ ...project, lanes })
      record(
        apply(state.project, after),
        {
          key: `lane:color:${laneId}`,
          label: '修改轨道颜色',
          timestamp: Date.now(),
          merge: false,
          undo: (project) => apply(project, before),
          redo: (project) => apply(project, after),
        },
      )
    },
    setLanePm3Track: (laneId, trackId) => {
      const state = get()
      const lane = state.project.lanes.find((item) => item.id === laneId)
      if (!lane || lane.kind === 'input') return
      if (trackId !== null && (trackId < 6 || trackId > 23 || trackId === 16)) return
      const before = state.project
      const lanes = before.lanes.map((item) => {
        if (item.id !== laneId) return item
        const pm3 = item.extensions.pm3 && typeof item.extensions.pm3 === 'object' && !Array.isArray(item.extensions.pm3)
          ? { ...item.extensions.pm3 as Record<string, unknown> }
          : {}
        if (trackId === null) delete pm3.track_id
        else pm3.track_id = trackId
        return {
          ...item,
          kind: trackId === null ? 'anonymous' as const : 'auxiliary' as const,
          extensions: { ...item.extensions, pm3 },
        }
      })
      const difficulties = Object.fromEntries(Object.entries(before.difficulties).map(([id, chart]) => [id, {
        ...chart,
        notes: chart.notes.map((note) => note.lane_id === laneId
          ? { ...note, playable: trackId === null ? note.playable : false }
          : note),
      }])) as SongProject['difficulties']
      const after = { ...before, lanes, difficulties }
      record(
        after,
        projectHistory('lane:pm3-track', trackId === null ? '取消 PM3 辅助分类' : `映射到 PM3 Track ${trackId}`, before, after),
      )
    },

    createAnonymousLane: (sourceLaneId, range = null) => {
      const state = get()
      if (state.project.difficulties[state.activeDifficulty].locked) return null
      const nextId = Math.max(0, ...state.project.lanes.map((lane) => lane.id)) + 1
      if (nextId > 255) return null
      const anonymousIndex = state.project.lanes.filter((lane) => lane.kind === 'anonymous').length + 1
      const palette = ['#8aa1a8', '#79a9c7', '#a493c7', '#79b59d', '#c49a75', '#b78c9c']
      const lane: Lane = {
        id: nextId,
        code: `anonymous_${nextId}`,
        display_name: `匿名 Track ${anonymousIndex}`,
        color: palette[(anonymousIndex - 1) % palette.length],
        hand: 'either',
        kind: 'anonymous',
        default_key_sound_id: null,
        muted: false,
        extensions: { editor: { created: true } },
      }
      const before = state.project
      const chart = before.difficulties[state.activeDifficulty]
      const movedIds = new Set<string>()
      const notes = chart.notes.map((note) => {
        if (sourceLaneId === undefined || note.lane_id !== sourceLaneId || !inRange(note.pulse, range)) return note
        movedIds.add(note.id)
        return { ...note, lane_id: nextId, playable: false }
      })
      const after: SongProject = {
        ...before,
        lanes: [...before.lanes, lane],
        difficulties: {
          ...before.difficulties,
          [state.activeDifficulty]: { ...chart, notes },
        },
      }
      record(
        after,
        projectHistory('lane:create', sourceLaneId === undefined ? '新建匿名 Track' : '新建匿名 Track 并迁移', before, after),
        movedIds,
        { activeLaneId: nextId },
      )
      return nextId
    },

    moveLaneNotes: (sourceLaneId, targetLaneId, range = null) => {
      const state = get()
      const chart = state.project.difficulties[state.activeDifficulty]
      if (chart.locked || sourceLaneId === targetLaneId) return
      const movedIds = new Set<string>()
      const targetPlayable = state.project.lanes.find((lane) => lane.id === targetLaneId)?.kind === 'input'
      const notes = chart.notes.map((note) => {
        if (note.lane_id !== sourceLaneId || !inRange(note.pulse, range)) return note
        movedIds.add(note.id)
        return { ...note, lane_id: targetLaneId, playable: targetPlayable }
      })
      if (!movedIds.size) return
      const before = state.project
      const after: SongProject = {
        ...before,
        difficulties: { ...before.difficulties, [state.activeDifficulty]: { ...chart, notes } },
      }
      record(after, projectHistory('lane:move', '迁移 Track 音符', before, after), movedIds, { activeLaneId: targetLaneId })
    },

    swapLaneNotes: (sourceLaneId, targetLaneId, range = null) => {
      const state = get()
      const chart = state.project.difficulties[state.activeDifficulty]
      if (chart.locked || sourceLaneId === targetLaneId) return
      const swappedIds = new Set<string>()
      const sourcePlayable = state.project.lanes.find((lane) => lane.id === sourceLaneId)?.kind === 'input'
      const targetPlayable = state.project.lanes.find((lane) => lane.id === targetLaneId)?.kind === 'input'
      const notes = chart.notes.map((note) => {
        if (!inRange(note.pulse, range)) return note
        if (note.lane_id === sourceLaneId) {
          swappedIds.add(note.id)
          return { ...note, lane_id: targetLaneId, playable: targetPlayable }
        }
        if (note.lane_id === targetLaneId) {
          swappedIds.add(note.id)
          return { ...note, lane_id: sourceLaneId, playable: sourcePlayable }
        }
        return note
      })
      if (!swappedIds.size) return
      const before = state.project
      const after: SongProject = {
        ...before,
        difficulties: { ...before.difficulties, [state.activeDifficulty]: { ...chart, notes } },
      }
      record(after, projectHistory('lane:swap', '交换 Track 音符', before, after), swappedIds, { activeLaneId: targetLaneId })
    },

    mergeLaneInto: (sourceLaneId, targetLaneId, range = null) => {
      const state = get()
      const chart = state.project.difficulties[state.activeDifficulty]
      if (chart.locked || sourceLaneId === targetLaneId) return
      const moving = chart.notes.filter((note) => note.lane_id === sourceLaneId && inRange(note.pulse, range))
      if (!moving.length) return
      const movingIds = new Set(moving.map((note) => note.id))
      const targetPlayable = state.project.lanes.find((lane) => lane.id === targetLaneId)?.kind === 'input'
      const moved = chart.notes.map((note) => movingIds.has(note.id)
        ? { ...note, lane_id: targetLaneId, playable: targetPlayable }
        : note)
      const winnerByPulse = new Map<number, string>()
      for (const note of moved) {
        if (note.lane_id !== targetLaneId || !inRange(note.pulse, range)) continue
        const winner = winnerByPulse.get(note.pulse)
        if (!winner || (movingIds.has(winner) && !movingIds.has(note.id))) winnerByPulse.set(note.pulse, note.id)
      }
      const candidateIds = new Set(
        moved
          .filter((note) => note.lane_id === targetLaneId && inRange(note.pulse, range))
          .map((note) => note.id),
      )
      const winnerIds = new Set(winnerByPulse.values())
      const notes = moved.filter((note) => !candidateIds.has(note.id) || winnerIds.has(note.id))
      const before = state.project
      let after: SongProject = {
        ...before,
        difficulties: { ...before.difficulties, [state.activeDifficulty]: { ...chart, notes } },
      }
      const source = after.lanes.find((lane) => lane.id === sourceLaneId)
      const sourceStillUsed = Object.values(after.difficulties).some((difficultyChart) => (
        difficultyChart.notes.some((note) => note.lane_id === sourceLaneId)
      ))
      if (source?.kind !== 'input' && !sourceStillUsed) {
        after = removeLaneMappings({ ...after, lanes: after.lanes.filter((lane) => lane.id !== sourceLaneId) }, sourceLaneId)
      }
      record(after, projectHistory('lane:merge', '合并 Track 并去重冲突', before, after), winnerIds, { activeLaneId: targetLaneId })
    },

    removeEmptyAnonymousLane: (laneId) => {
      const state = get()
      const lane = state.project.lanes.find((item) => item.id === laneId)
      const used = Object.values(state.project.difficulties).some((chart) => chart.notes.some((note) => note.lane_id === laneId))
      if (!lane || lane.kind === 'input' || used) return
      const before = state.project
      const after = removeLaneMappings({ ...before, lanes: before.lanes.filter((item) => item.id !== laneId) }, laneId)
      record(after, projectHistory('lane:remove', '删除空 Track', before, after), new Set(), {
        activeLaneId: after.lanes[0]?.id ?? null,
      })
    },

    beginNoteTransaction: (ids) => {
      const state = get()
      if (state.editTransaction) return
      const chart = state.project.difficulties[state.activeDifficulty]
      if (chart.locked) return
      const before = noteSnapshots(chart.notes, new Set(ids))
      if (before.length) set({ editTransaction: { difficulty: state.activeDifficulty, before, after: before } })
    },
    previewMoveTransaction: (deltaPulse, laneDelta) => {
      const state = get()
      const transaction = state.editTransaction
      if (!transaction) return
      const laneIds = state.project.lanes.map((lane) => lane.id)
      const after = transaction.before.map((snapshot) => {
        const note = snapshot.note as Note
        const laneIndex = laneIds.indexOf(note.lane_id)
        const targetLaneId = laneIds[Math.min(laneIds.length - 1, Math.max(0, laneIndex + laneDelta))]
        return {
          ...snapshot,
          note: {
            ...note,
            pulse: Math.max(0, Math.round(note.pulse + deltaPulse)),
            lane_id: targetLaneId,
            playable: state.project.lanes.find((lane) => lane.id === targetLaneId)?.kind === 'input',
          },
        }
      })
      set({
        project: applyNoteSnapshots(state.project, transaction.difficulty, after),
        editTransaction: { ...transaction, after },
      })
    },
    previewResizeTransaction: (noteId, length) => {
      const state = get()
      const transaction = state.editTransaction
      if (!transaction) return
      const after = transaction.before.map((snapshot) => ({
        ...snapshot,
        note: snapshot.id === noteId
          ? { ...(snapshot.note as Note), length: Math.max(0, Math.round(length)) }
          : snapshot.note,
      }))
      set({
        project: applyNoteSnapshots(state.project, transaction.difficulty, after),
        editTransaction: { ...transaction, after },
      })
    },
    finishNoteTransaction: () => {
      const state = get()
      const transaction = state.editTransaction
      if (!transaction) return
      if (sameSnapshots(transaction.before, transaction.after)) {
        set({ editTransaction: null })
        return
      }
      record(
        state.project,
        noteHistory('notes:drag', '拖动音符', transaction.difficulty, transaction.before, transaction.after),
        state.selectedIds,
        { editTransaction: null },
      )
    },
    cancelNoteTransaction: () => {
      const state = get()
      const transaction = state.editTransaction
      if (!transaction) return
      set({
        project: applyNoteSnapshots(state.project, transaction.difficulty, transaction.before),
        editTransaction: null,
      })
    },

    undo: () => {
      const state = get()
      if (state.editTransaction) {
        set({
          project: applyNoteSnapshots(state.project, state.editTransaction.difficulty, state.editTransaction.before),
          editTransaction: null,
        })
        return
      }
      const entry = state.undoStack.at(-1)
      if (!entry) return
      const project = touched(entry.undo(state.project))
      set({
        project,
        undoStack: state.undoStack.slice(0, -1),
        redoStack: [...state.redoStack.slice(-49), entry],
        selectedIds: new Set(),
        saveStatus: 'dirty',
        activeLaneId: project.lanes.some((lane) => lane.id === state.activeLaneId)
          ? state.activeLaneId
          : project.lanes[0]?.id ?? null,
      })
    },
    redo: () => {
      const state = get()
      const entry = state.redoStack.at(-1)
      if (!entry) return
      const project = touched(entry.redo(state.project))
      set({
        project,
        undoStack: [...state.undoStack.slice(-49), entry],
        redoStack: state.redoStack.slice(0, -1),
        selectedIds: new Set(),
        saveStatus: 'dirty',
        activeLaneId: project.lanes.some((lane) => lane.id === state.activeLaneId)
          ? state.activeLaneId
          : project.lanes[0]?.id ?? null,
      })
    },
  }
})
