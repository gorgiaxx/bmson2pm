import type { DifficultyId } from './types'

export const PM3_RESERVED_SONG_SLOTS: Readonly<Record<number, Partial<Record<DifficultyId, number>>>> = {
  134: { easy: 4, normal: 4, hard: 4, special: 4, master: 4 },
  150: { easy: 0, normal: 0, hard: 0, special: 0, master: 0 },
  153: { easy: 3, normal: 3, hard: 3, special: 3, master: 3 },
  154: { easy: 4, normal: 4, hard: 4, special: 4, master: 4 },
  155: { easy: 5, normal: 5, hard: 5, special: 5, master: 5 },
  156: { easy: 6, normal: 6, hard: 6, special: 6, master: 6 },
  157: { easy: 7, normal: 7, hard: 7, special: 7, master: 7 },
}

export const PM3_RESERVED_SONG_IDS = Object.keys(PM3_RESERVED_SONG_SLOTS).map(Number)

export function pm3ReservedSlot(songId: number, difficulty: DifficultyId): number | null {
  return PM3_RESERVED_SONG_SLOTS[songId]?.[difficulty] ?? null
}

export function pm3CompatibleReservedIds(difficulties: DifficultyId[]): number[] {
  return PM3_RESERVED_SONG_IDS.filter((songId) => (
    difficulties.every((difficulty) => pm3ReservedSlot(songId, difficulty) !== null)
  ))
}
