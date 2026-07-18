import { DEFAULT_LANES, DIFFICULTIES } from './constants'
import type { DifficultyId, Note, SongProject } from './types'

const uid = (): string => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`

function makeNotes(difficulty: DifficultyId, density: number): Note[] {
  const notes: Note[] = []
  const resolution = 240
  for (let bar = 0; bar < 24; bar += 1) {
    const steps = Math.max(2, Math.round(4 * density))
    for (let step = 0; step < steps; step += 1) {
      if ((bar + step) % 7 === 0 && difficulty === 'easy') continue
      const pulse = bar * resolution * 4 + Math.round(step * resolution * 4 / steps)
      const lane = ((bar * 2 + step) % 6) + 1
      notes.push({
        id: uid(), lane_id: lane, pulse, length: 0, key_sound_id: null,
        volume: 1, playable: true, continues: false, source: 'template', notes: '', extensions: {},
      })
      if (density > 1.35 && step === 0 && bar % 3 === 0) {
        notes.push({
          id: uid(), lane_id: lane % 2 === 0 ? 5 : 6, pulse, length: 0,
          key_sound_id: null, volume: 1, playable: true, continues: false,
          source: 'template', notes: '', extensions: {},
        })
      }
    }
  }
  return notes
}

export function createDemoProject(id: string = uid()): SongProject {
  const now = new Date().toISOString()
  const densities: Record<DifficultyId, number> = {
    easy: 0.5, normal: 0.75, hard: 1, special: 1.5, master: 2,
  }
  const levels: Record<DifficultyId, number> = { easy: 3, normal: 6, hard: 9, special: 12, master: 15 }
  const difficulties = Object.fromEntries(DIFFICULTIES.map(({ id, name }) => [id, {
    id,
    display_name: name,
    level: levels[id],
    notes: makeNotes(id, densities[id]),
    locked: false,
    description: '',
    extensions: {},
  }])) as SongProject['difficulties']
  return {
    schema_version: '1.3', id,
    metadata: {
      title: 'Neon Pulse', artist: 'BMSON2PM Demo', subtitle: '', game_song_id: null,
      version: 'MVP', audio_duration: 48, preview_time: 0, import_format: 'platform',
      source_name: null, notes: '内置示例项目',
    },
    timing: {
      resolution: 240, initial_bpm: 128, audio_offset_ms: 0, chart_offset_ms: 0,
      key_sound_offset_ms: 0, mv_offset_ms: 0, bpm_events: [], stop_events: [],
      bar_lines: Array.from({ length: 25 }, (_, index) => ({
        id: `bar-${index}`, pulse: index * 960, extensions: {},
      })),
    },
    lanes: structuredClone(DEFAULT_LANES), difficulties, audio_assets: [], key_sounds: [],
    mv_configuration: {}, game_specific_data: { lane_semantics: 'pm3-six-input-v3' }, source_files: [], unknown_data: {},
    version_history: [], created_at: now, updated_at: now,
  }
}
