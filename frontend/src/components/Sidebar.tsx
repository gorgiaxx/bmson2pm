import { ChevronRight, GitCompareArrows, Lock, Music2, SlidersHorizontal, Unlock } from 'lucide-react'
import { DIFFICULTIES } from '../constants'
import { useEditorStore } from '../store'
import type { DifficultyId } from '../types'

export function Sidebar() {
  const project = useEditorStore((state) => state.project)
  const active = useEditorStore((state) => state.activeDifficulty)
  const setActive = useEditorStore((state) => state.setActiveDifficulty)
  const reference = useEditorStore((state) => state.referenceDifficulty)
  const setReference = useEditorStore((state) => state.setReferenceDifficulty)
  const activeLaneId = useEditorStore((state) => state.activeLaneId)
  const setActiveLane = useEditorStore((state) => state.setActiveLane)
  const updateDifficulty = useEditorStore((state) => state.updateDifficulty)
  const inputLaneIds = new Set(project.lanes.filter((lane) => lane.kind === 'input').map((lane) => lane.id))

  return (
    <aside className="sidebar">
      <section className="side-section project-summary">
        <div className="eyebrow"><Music2 size={13} />当前项目</div>
        <strong>{project.metadata.title}</strong>
        <span>{project.metadata.artist}</span>
        <div className="project-tags">
          <span>{project.metadata.import_format.toUpperCase()}</span>
          <span>{project.timing.initial_bpm} BPM</span>
        </div>
      </section>

      <section className="side-section difficulty-section">
        <div className="section-title"><span>难度</span><span>LV / NOTES</span></div>
        <div className="difficulty-list">
          {DIFFICULTIES.map((item) => {
            const chart = project.difficulties[item.id]
            const inputNoteCount = chart.notes.filter((note) => inputLaneIds.has(note.lane_id)).length
            return (
              <button
                type="button"
                key={item.id}
                className={`difficulty-row ${active === item.id ? 'active' : ''}`}
                onClick={() => setActive(item.id)}
              >
                <span className={`difficulty-code ${item.id}`}>{item.short}</span>
                <span className="difficulty-name">{item.name}</span>
                <span className="difficulty-data"><b>{chart.level}</b><small>{inputNoteCount}</small></span>
                <ChevronRight size={14} />
              </button>
            )
          })}
        </div>
      </section>

      <section className="side-section comparison-section">
        <div className="eyebrow"><GitCompareArrows size={13} />对照难度</div>
        <select value={reference ?? ''} onChange={(event) => setReference((event.target.value || null) as DifficultyId | null)}>
          <option value="">不显示参考谱面</option>
          {DIFFICULTIES.filter((item) => item.id !== active).map((item) => (
            <option key={item.id} value={item.id}>{item.name}</option>
          ))}
        </select>
        <p>参考音符以虚线叠加，不参与编辑。</p>
      </section>

      <section className="side-section lane-legend">
        <div className="section-title"><span>Tracks</span><SlidersHorizontal size={13} /></div>
        {project.lanes.map((lane) => {
          const pm3 = lane.extensions.pm3 && typeof lane.extensions.pm3 === 'object' && !Array.isArray(lane.extensions.pm3)
            ? lane.extensions.pm3 as Record<string, unknown>
            : null
          const pm3Track = typeof pm3?.track_id === 'number' ? pm3.track_id : null
          const label = lane.kind === 'auxiliary'
            ? `P${pm3Track ?? '?'}`
            : lane.kind === 'anonymous'
              ? 'A'
              : lane.hand === 'left' ? 'L' : lane.hand === 'right' ? 'R' : lane.hand === 'both' ? '双' : '单'
          return (
            <button
              type="button"
              className={`lane-legend-row ${activeLaneId === lane.id ? 'active' : ''} ${lane.kind}`}
              key={lane.id}
              onClick={() => setActiveLane(lane.id)}
            >
              <i style={{ background: lane.color }} />
              <span>{lane.display_name}</span>
              <small>{label}</small>
            </button>
          )
        })}
      </section>

      <button
        type="button"
        className="lock-chart"
        onClick={() => updateDifficulty(active, { locked: !project.difficulties[active].locked })}
      >
        {project.difficulties[active].locked ? <Lock size={14} /> : <Unlock size={14} />}
        {project.difficulties[active].locked ? '正式难度已锁定' : '锁定当前难度'}
      </button>
    </aside>
  )
}
