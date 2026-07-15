import { AlertCircle, BarChart3, CheckCircle2, Copy, Film, Info, ListChecks, SlidersHorizontal, TriangleAlert } from 'lucide-react'
import { useMemo, useState } from 'react'
import { DIFFICULTIES } from '../constants'
import { BgaPreview } from './BgaPreview'
import { useEditorStore } from '../store'
import { calculateStats } from '../timing'
import type { DifficultyId, ValidationIssue } from '../types'

type InspectorTab = 'properties' | 'issues' | 'stats' | 'bga'

interface InspectorProps {
  issues: ValidationIssue[]
  validating: boolean
  onValidate: () => void
  position: number
  playing: boolean
  speed: number
}

export function Inspector({ issues, validating, onValidate, position, playing, speed }: InspectorProps) {
  const [tab, setTab] = useState<InspectorTab>('properties')
  const [copyTarget, setCopyTarget] = useState<DifficultyId>('normal')
  const project = useEditorStore((state) => state.project)
  const difficulty = useEditorStore((state) => state.activeDifficulty)
  const selectedIds = useEditorStore((state) => state.selectedIds)
  const updateSelected = useEditorStore((state) => state.updateSelected)
  const updateMetadata = useEditorStore((state) => state.updateMetadata)
  const updateTiming = useEditorStore((state) => state.updateTiming)
  const updateDifficulty = useEditorStore((state) => state.updateDifficulty)
  const copyDifficulty = useEditorStore((state) => state.copyDifficulty)
  const selectOnly = useEditorStore((state) => state.selectOnly)
  const setScrollPulse = useEditorStore((state) => state.setScrollPulse)
  const chart = project.difficulties[difficulty]
  const selectedNotes = useMemo(() => chart.notes.filter((note) => selectedIds.has(note.id)), [chart.notes, selectedIds])
  const laneById = useMemo(() => new Map(project.lanes.map((lane) => [lane.id, lane])), [project.lanes])
  const inputNotes = useMemo(
    () => chart.notes.filter((note) => laneById.get(note.lane_id)?.kind === 'input'),
    [chart.notes, laneById],
  )
  const stats = useMemo(() => calculateStats(project, inputNotes), [inputNotes, project])
  const selectedKinds = new Set(selectedNotes.map((note) => laneById.get(note.lane_id)?.kind ?? 'anonymous'))
  const selectedRole = selectedKinds.size > 1
    ? '混合 Track'
    : selectedKinds.has('input')
      ? '玩家判定'
      : selectedKinds.has('auxiliary')
        ? '辅助事件'
        : '待分类事件'
  const errorCount = issues.filter((issue) => issue.severity === 'error').length
  const warningCount = issues.filter((issue) => issue.severity === 'warning').length

  return (
    <aside className="inspector">
      <div className="inspector-tabs" role="tablist">
        <button type="button" className={tab === 'properties' ? 'active' : ''} onClick={() => setTab('properties')} title="属性"><SlidersHorizontal size={15} /></button>
        <button type="button" className={tab === 'issues' ? 'active' : ''} onClick={() => setTab('issues')} title="验证">
          <ListChecks size={15} />{issues.length > 0 && <i>{issues.length}</i>}
        </button>
        <button type="button" className={tab === 'stats' ? 'active' : ''} onClick={() => setTab('stats')} title="统计"><BarChart3 size={15} /></button>
        <button type="button" className={tab === 'bga' ? 'active' : ''} onClick={() => setTab('bga')} title="BGA 预览"><Film size={15} /></button>
      </div>

      {tab === 'properties' && (
        <div className="inspector-body">
          <div className="inspector-heading">
            <div><span>属性</span><strong>{selectedNotes.length ? `${selectedNotes.length} 个音符` : '歌曲与难度'}</strong></div>
            {selectedNotes.length > 0 && <span className="selection-count">{selectedNotes.length}</span>}
          </div>
          {selectedNotes.length > 0 ? (
            <>
              <fieldset className="property-group">
                <legend>音符</legend>
                <label><span>Lane</span>
                  <select
                    value={selectedNotes.every((note) => note.lane_id === selectedNotes[0].lane_id) ? selectedNotes[0].lane_id : ''}
                    onChange={(event) => {
                      const laneId = Number(event.target.value)
                      updateSelected({ lane_id: laneId, playable: laneById.get(laneId)?.kind === 'input' })
                    }}
                  >
                    {!selectedNotes.every((note) => note.lane_id === selectedNotes[0].lane_id) && <option value="">多个值</option>}
                    {project.lanes.map((lane) => <option key={lane.id} value={lane.id}>{lane.display_name}</option>)}
                  </select>
                </label>
                <label><span>Pulse</span>
                  <input
                    type="number"
                    value={selectedNotes.length === 1 ? selectedNotes[0].pulse : ''}
                    placeholder={selectedNotes.length > 1 ? '多个值' : ''}
                    onChange={(event) => updateSelected({ pulse: Math.max(0, Number(event.target.value)) })}
                    disabled={selectedNotes.length > 1}
                  />
                </label>
                <label><span>长度</span>
                  <input type="number" min="0" value={selectedNotes.length === 1 ? selectedNotes[0].length : ''} placeholder="多个值" onChange={(event) => updateSelected({ length: Math.max(0, Number(event.target.value)) })} />
                </label>
                <label><span>音量</span><div className="range-with-value">
                  <input type="range" min="0" max="2" step="0.05" value={selectedNotes[0].volume} onChange={(event) => updateSelected({ volume: Number(event.target.value) })} />
                  <b>{Math.round(selectedNotes[0].volume * 100)}%</b>
                </div></label>
                <label><span>Key 音</span>
                  <select value={selectedNotes[0].key_sound_id ?? ''} onChange={(event) => updateSelected({ key_sound_id: event.target.value || null })}>
                    <option value="">使用 Lane 默认</option>
                    {project.key_sounds.map((sound) => <option key={sound.id} value={sound.id}>{sound.name}</option>)}
                  </select>
                </label>
                <label className="checkbox-row"><input type="checkbox" checked={selectedKinds.size === 1 && selectedKinds.has('input')} readOnly disabled /><span>{selectedRole}</span></label>
              </fieldset>
              <fieldset className="property-group">
                <legend>备注</legend>
                <textarea value={selectedNotes.length === 1 ? selectedNotes[0].notes : ''} placeholder={selectedNotes.length > 1 ? '批量写入备注' : '添加编排备注'} onChange={(event) => updateSelected({ notes: event.target.value })} />
              </fieldset>
            </>
          ) : (
            <>
              <fieldset className="property-group">
                <legend>歌曲</legend>
                <label><span>标题</span><input value={project.metadata.title} onChange={(event) => updateMetadata({ title: event.target.value })} /></label>
                <label><span>艺术家</span><input value={project.metadata.artist} onChange={(event) => updateMetadata({ artist: event.target.value })} /></label>
                <label><span>初始 BPM</span><input type="number" min="1" max="1000" step="0.01" value={project.timing.initial_bpm} onChange={(event) => updateTiming({ initial_bpm: Number(event.target.value) })} /></label>
                <label><span>分辨率</span><input type="number" min="24" value={project.timing.resolution} onChange={(event) => updateTiming({ resolution: Number(event.target.value) })} /></label>
                <label><span>谱面偏移</span><div className="unit-input"><input type="number" step="1" value={project.timing.chart_offset_ms} onChange={(event) => updateTiming({ chart_offset_ms: Number(event.target.value) })} /><i>ms</i></div></label>
                <label><span>Key 音偏移</span><div className="unit-input"><input type="number" step="1" value={project.timing.key_sound_offset_ms} onChange={(event) => updateTiming({ key_sound_offset_ms: Number(event.target.value) })} /><i>ms</i></div></label>
                <label><span>MV 偏移</span><div className="unit-input"><input type="number" step="1" value={project.timing.mv_offset_ms} onChange={(event) => updateTiming({ mv_offset_ms: Number(event.target.value) })} /><i>ms</i></div></label>
              </fieldset>
              <fieldset className="property-group">
                <legend>当前难度</legend>
                <label><span>等级</span><input type="number" min="0" max="99" value={chart.level} onChange={(event) => updateDifficulty(difficulty, { level: Number(event.target.value) })} /></label>
                <label><span>名称</span><input value={chart.display_name} onChange={(event) => updateDifficulty(difficulty, { display_name: event.target.value })} /></label>
                <label><span>复制到</span><div className="inline-control">
                  <select value={copyTarget} onChange={(event) => setCopyTarget(event.target.value as DifficultyId)}>
                    {DIFFICULTIES.filter((item) => item.id !== difficulty).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                  </select>
                  <button type="button" className="icon-button" onClick={() => copyDifficulty(difficulty, copyTarget)} title="覆盖目标难度"><Copy size={14} /></button>
                </div></label>
              </fieldset>
            </>
          )}
        </div>
      )}

      {tab === 'issues' && (
        <div className="inspector-body">
          <div className="inspector-heading"><div><span>谱面验证</span><strong>{errorCount ? `${errorCount} 个错误` : warningCount ? `${warningCount} 个警告` : '可以导出'}</strong></div></div>
          <button type="button" className="button validate-button" onClick={onValidate} disabled={validating}>
            {validating ? '正在检查…' : '重新验证当前难度'}
          </button>
          <div className="issue-summary">
            <span className="error"><AlertCircle size={13} />{errorCount} 错误</span>
            <span className="warning"><TriangleAlert size={13} />{warningCount} 警告</span>
            <span className="info"><Info size={13} />{issues.filter((issue) => issue.severity === 'info').length} 提示</span>
          </div>
          <div className="issue-list">
            {!issues.length && (
              <div className="empty-state"><CheckCircle2 size={28} /><strong>检查通过</strong><span>当前难度未发现问题</span></div>
            )}
            {issues.map((issue) => (
              <button type="button" key={issue.id} className={`issue-row ${issue.severity}`} onClick={() => {
                if (issue.note_id) selectOnly(issue.note_id)
                if (issue.pulse !== null) setScrollPulse(Math.max(0, issue.pulse - project.timing.resolution * 2))
              }}>
                {issue.severity === 'error' ? <AlertCircle size={15} /> : issue.severity === 'warning' ? <TriangleAlert size={15} /> : <Info size={15} />}
                <span><strong>{issue.message}</strong><small>{issue.pulse !== null ? `Pulse ${issue.pulse}` : issue.code}</small></span>
              </button>
            ))}
          </div>
        </div>
      )}

      {tab === 'stats' && (
        <div className="inspector-body">
          <div className="inspector-heading"><div><span>谱面统计</span><strong>{chart.display_name} · Lv.{chart.level}</strong></div></div>
          <div className="stats-grid">
            <div><span>玩家音符</span><strong>{stats.total}</strong></div>
            <div><span>平均 NPS</span><strong>{stats.nps.toFixed(2)}</strong></div>
            <div><span>同时敲击</span><strong>{Math.round(stats.simultaneousRate * 100)}%</strong></div>
            <div><span>最密集小节</span><strong>#{stats.denseBar}</strong></div>
          </div>
          <div className="metric-block"><div><span>小鼓左右平衡</span><b>L {Math.round(stats.leftRate * 100)} / R {100 - Math.round(stats.leftRate * 100)}</b></div><div className="balance-bar"><i style={{ width: `${stats.leftRate * 100}%` }} /></div></div>
          <div className="metric-block"><div><span>鼓缘使用率</span><b>{Math.round(stats.rimRate * 100)}%</b></div><div className="balance-bar rim"><i style={{ width: `${stats.rimRate * 100}%` }} /></div></div>
          <div className="lane-stats">
            <h3>Lane 分布</h3>
            {project.lanes.filter((lane) => lane.kind === 'input').map((lane) => {
              const value = stats.laneCounts[lane.id] ?? 0
              const max = Math.max(...Object.values(stats.laneCounts), 1)
              return <div key={lane.id}><span><i style={{ background: lane.color }} />{lane.display_name}</span><div><i style={{ width: `${value / max * 100}%`, background: lane.color }} /></div><b>{value}</b></div>
            })}
          </div>
          <div className="stat-footnote">辅助事件：{chart.notes.length - inputNotes.length} · 最短非同时音符间隔：{stats.minIntervalMs === null ? '—' : `${Math.round(stats.minIntervalMs)} ms`}</div>
        </div>
      )}

      {tab === 'bga' && (
        <BgaPreview project={project} position={position} playing={playing} speed={speed} />
      )}
    </aside>
  )
}
