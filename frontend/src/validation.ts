import { pulseToSeconds } from './timing'
import type { DifficultyId, Note, SongProject, ValidationIssue } from './types'

const issueId = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`

export function validateProjectLocally(project: SongProject, difficulty: DifficultyId): ValidationIssue[] {
  const issues: ValidationIssue[] = []
  const laneById = new Map(project.lanes.map((lane) => [lane.id, lane]))
  const laneIds = new Set(laneById.keys())
  const notes = project.difficulties[difficulty].notes
  const idCounts = new Map<string, number>()
  const byLane = new Map<number, Note[]>()
  const simultaneous = new Map<number, number>()

  for (const note of notes) {
    idCounts.set(note.id, (idCounts.get(note.id) ?? 0) + 1)
    const laneNotes = byLane.get(note.lane_id) ?? []
    laneNotes.push(note)
    byLane.set(note.lane_id, laneNotes)
    if (laneById.get(note.lane_id)?.kind === 'input') {
      simultaneous.set(note.pulse, (simultaneous.get(note.pulse) ?? 0) + 1)
    }
  }

  for (const note of notes) {
    if ((idCounts.get(note.id) ?? 0) > 1) {
      issues.push(makeIssue('error', 'note.duplicate_id', '音符 ID 重复', difficulty, note))
    }
    if (!laneIds.has(note.lane_id)) {
      issues.push(makeIssue('error', 'note.invalid_lane', `Lane ${note.lane_id} 不存在`, difficulty, note))
    }
    if (project.metadata.audio_duration > 0 && pulseToSeconds(project, note.pulse) > project.metadata.audio_duration) {
      issues.push(makeIssue('warning', 'note.after_audio', '音符超出音乐时长', difficulty, note))
    }
  }

  for (const [laneId, laneNotes] of byLane) {
    if (laneById.get(laneId)?.kind !== 'input') continue
    const ordered = [...laneNotes].sort((a, b) => a.pulse - b.pulse || a.length - b.length || a.id.localeCompare(b.id))
    for (let index = 1; index < ordered.length; index += 1) {
      const left = ordered[index - 1]
      const right = ordered[index]
      if (left.pulse === right.pulse) {
        const uniform = left.length === right.length
        issues.push(makeIssue(
          uniform ? 'warning' : 'error',
          uniform ? 'note.layered' : 'note.nonuniform_layer',
          uniform ? `Lane ${laneId} 同一位置存在叠音` : `Lane ${laneId} 同一位置的叠音长度不一致`,
          difficulty,
          right,
        ))
        continue
      }
      if (right.pulse - left.pulse < project.timing.resolution / 8) {
        issues.push(makeIssue('warning', 'playability.close_notes', `Lane ${laneId} 的音符间隔小于三十二分音符`, difficulty, right))
      }
    }
    let activeLongNotes: Note[] = []
    for (const note of ordered) {
      activeLongNotes = activeLongNotes.filter((active) => active.pulse + active.length > note.pulse)
      if (activeLongNotes.some((active) => active.pulse < note.pulse)) {
        issues.push(makeIssue('error', 'note.overlap', `Lane ${laneId} 的长音符区间互相重叠`, difficulty, note))
      }
      if (note.length > 0) activeLongNotes.push(note)
    }
  }

  for (const [pulse, count] of simultaneous) {
    if (count <= 2) continue
    issues.push({
      id: issueId(), severity: 'warning', code: 'playability.too_many_simultaneous',
      message: `同一时刻有 ${count} 个可操作音符`, difficulty, note_id: null, pulse,
    })
  }
  const anonymousLaneIds = new Set(project.lanes.filter((lane) => lane.kind === 'anonymous').map((lane) => lane.id))
  const anonymousNotes = notes.filter((note) => anonymousLaneIds.has(note.lane_id))
  if (anonymousNotes.length) {
    issues.push({
      id: issueId(), severity: 'info', code: 'track.anonymous_notes',
      message: `${anonymousNotes.length} 个事件仍在待分类 Track，导出 PM3 前需迁移或指定辅助 Track`,
      difficulty, note_id: null, pulse: null,
    })
  }
  return issues
}

function makeIssue(
  severity: ValidationIssue['severity'],
  code: string,
  message: string,
  difficulty: DifficultyId,
  note: Note,
): ValidationIssue {
  return { id: issueId(), severity, code, message, difficulty, note_id: note.id, pulse: note.pulse }
}
