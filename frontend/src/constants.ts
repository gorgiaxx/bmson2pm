import type { DifficultyId, Lane } from './types'

export const DIFFICULTIES: Array<{ id: DifficultyId; name: string; short: string }> = [
  { id: 'easy', name: '初级', short: 'EZ' },
  { id: 'normal', name: '中级', short: 'NM' },
  { id: 'hard', name: '高级', short: 'HD' },
  { id: 'special', name: '超高级', short: 'SP' },
  { id: 'master', name: '大师级', short: 'MX' },
]

export const DEFAULT_LANES: Lane[] = [
  { id: 1, code: 'small_left', display_name: '左小鼓', color: '#40c4b4', hand: 'left', kind: 'input', default_key_sound_id: null, muted: false, extensions: {} },
  { id: 2, code: 'small_right', display_name: '右小鼓', color: '#e96978', hand: 'right', kind: 'input', default_key_sound_id: null, muted: false, extensions: {} },
  { id: 3, code: 'rim_simultaneous', display_name: '鼓缘同时击打', color: '#62a6e8', hand: 'both', kind: 'input', default_key_sound_id: null, muted: false, extensions: {} },
  { id: 4, code: 'rim_single', display_name: '鼓缘单击', color: '#dc84d8', hand: 'either', kind: 'input', default_key_sound_id: null, muted: false, extensions: {} },
  { id: 5, code: 'head_simultaneous', display_name: '鼓面同时击打', color: '#f2aa4f', hand: 'both', kind: 'input', default_key_sound_id: null, muted: false, extensions: {} },
  { id: 6, code: 'head_single', display_name: '鼓面单击', color: '#e9d35b', hand: 'either', kind: 'input', default_key_sound_id: null, muted: false, extensions: {} },
]

export const TRACK_COLOR_PALETTE = [
  '#40c4b4', '#26a69a', '#66bb6a', '#9ccc65', '#b6c16b', '#e9d35b',
  '#f2aa4f', '#ee8b55', '#e96978', '#ef5350', '#ec6b9c', '#dc84d8',
  '#ab7bd5', '#8878d2', '#62a6e8', '#42a5d9', '#4fb8c5', '#79b59d',
  '#8aa1a8', '#78909c', '#a493c7', '#b78c9c', '#c49a75', '#d8b85f',
] as const

export const PM3_INPUT_TRACK_BY_LANE: Record<number, number> = {
  1: 2,
  2: 3,
  3: 4,
  4: 5,
  5: 0,
  6: 1,
}

export const PM3_AUXILIARY_TRACK_IDS = Array.from(
  { length: 18 },
  (_, index) => index + 6,
).filter((trackId) => trackId !== 16)

const LEGACY_LANE_CODES = new Set(['rim_left', 'rim_right', 'head_left', 'head_right'])

export function migrateLaneSemantics(lanes: Lane[]): Lane[] {
  const canonical = new Map(DEFAULT_LANES.map((lane) => [lane.id, lane]))
  return lanes.map((lane) => {
    const replacement = canonical.get(lane.id)
    const normalized = {
      ...lane,
      kind: lane.kind ?? (lane.id <= 6 ? 'input' : 'anonymous'),
      extensions: lane.extensions ?? {},
    }
    if (!replacement || !LEGACY_LANE_CODES.has(lane.code)) return normalized
    return {
      ...normalized,
      code: replacement.code,
      display_name: replacement.display_name,
      hand: replacement.hand,
    }
  })
}

export const QUANTIZATIONS = [
  { label: '1/4', divisor: 1 },
  { label: '1/8', divisor: 2 },
  { label: '1/12', divisor: 3 },
  { label: '1/16', divisor: 4 },
  { label: '1/24', divisor: 6 },
  { label: '1/32', divisor: 8 },
]
