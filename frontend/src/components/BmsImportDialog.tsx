import { AlertTriangle, ArrowRight, FileText, FolderOpen, Image as ImageIcon, LoaderCircle, Music2, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { bmsResourcePath } from '../api'
import type { BmsImportOptions, BmsInspection } from '../types'

const TEMPLATE_KEY = 'bmson2pm:bms-lane-map'
const LANES = [
  { id: 1, name: '左小鼓', color: '#40c4b4' },
  { id: 2, name: '右小鼓', color: '#e96978' },
  { id: 3, name: '鼓缘同时击打', color: '#62a6e8' },
  { id: 4, name: '鼓缘单击', color: '#dc84d8' },
  { id: 5, name: '鼓面同时击打', color: '#f2aa4f' },
  { id: 6, name: '鼓面单击', color: '#e9d35b' },
]

interface BmsImportDialogProps {
  inspection: BmsInspection
  chartFiles: File[]
  selectedChartPath: string
  resourceFiles: File[]
  busy: boolean
  onClose: () => void
  onEncodingChange: (encoding: string) => void
  onChartChange: (path: string) => void
  onResourceFiles: (files: File[]) => void
  onConfirm: (options: BmsImportOptions) => void
}

function withoutExtension(path: string): string {
  const normalized = path.replaceAll('\\', '/').replace(/^\.\//, '').toLowerCase()
  const slash = normalized.lastIndexOf('/')
  const dot = normalized.lastIndexOf('.')
  return dot > slash ? normalized.slice(0, dot) : normalized
}

function matchedResourceCount(
  definitions: Array<{ filename: string }>,
  files: File[],
): number {
  const paths = files.map((file) => bmsResourcePath(file).replaceAll('\\', '/').toLowerCase())
  const exact = new Set(paths)
  const stems = new Set(paths.map(withoutExtension))
  const basenameStems = new Set(paths.map((path) => withoutExtension(path.split('/').at(-1) ?? path)))
  return definitions.filter((item) => {
    const declared = item.filename.replaceAll('\\', '/').replace(/^\.\//, '').toLowerCase()
    return exact.has(declared)
      || stems.has(withoutExtension(declared))
      || basenameStems.has(withoutExtension(declared.split('/').at(-1) ?? declared))
  }).length
}

function defaultAssignments(inspection: BmsInspection): Record<number, string> {
  const knownChannels = new Set(inspection.playable_channels.map((item) => item.channel))
  try {
    const saved = JSON.parse(localStorage.getItem(TEMPLATE_KEY) ?? '{}') as Record<string, unknown>
    const fromTemplate: Record<number, string> = {}
    for (const [channel, lane] of Object.entries(saved)) {
      if (knownChannels.has(channel) && typeof lane === 'number' && lane >= 1 && lane <= 6) {
        fromTemplate[lane] = channel
      }
    }
    if (Object.keys(fromTemplate).length) return fromTemplate
  } catch { /* ignore invalid local template */ }

  const assignments: Record<number, string> = {}
  for (const channel of inspection.playable_channels) {
    if (channel.default_lane) assignments[channel.default_lane] = channel.channel
  }
  return assignments
}

export function BmsImportDialog({
  inspection,
  chartFiles,
  selectedChartPath,
  resourceFiles,
  busy,
  onClose,
  onEncodingChange,
  onChartChange,
  onResourceFiles,
  onConfirm,
}: BmsImportDialogProps) {
  const resourceInputRef = useRef<HTMLInputElement>(null)
  const [assignments, setAssignments] = useState<Record<number, string>>(() => defaultAssignments(inspection))
  const [randomValues, setRandomValues] = useState<Record<number, number>>(() => Object.fromEntries(
    inspection.random_blocks.map((block) => [block.index, block.selected]),
  ))
  const [saveTemplate, setSaveTemplate] = useState(true)
  const [preserveUnmapped, setPreserveUnmapped] = useState(true)

  useEffect(() => {
    setAssignments(defaultAssignments(inspection))
    setRandomValues(Object.fromEntries(inspection.random_blocks.map((block) => [block.index, block.selected])))
  }, [inspection])

  const selectedChannels = useMemo(() => new Set(Object.values(assignments).filter(Boolean)), [assignments])
  const selectedCandidate = inspection.encoding_candidates.find((item) => item.encoding === inspection.encoding)
  const canImport = inspection.playable_channels.length === 0 || selectedChannels.size > 0 || preserveUnmapped
  const anonymousCount = Math.max(0, inspection.playable_channels.length - selectedChannels.size)
  const audioResourceMatches = useMemo(
    () => matchedResourceCount(inspection.wav_files, resourceFiles),
    [inspection, resourceFiles],
  )
  const visualResourceMatches = useMemo(
    () => matchedResourceCount(inspection.bmp_files, resourceFiles),
    [inspection, resourceFiles],
  )
  const resourcesComplete = audioResourceMatches === inspection.wav_count
    && visualResourceMatches === inspection.bmp_count

  const assignChannel = (laneId: number, channel: string) => {
    setAssignments((current) => {
      const next = { ...current }
      for (const [lane, assigned] of Object.entries(next)) {
        if (assigned === channel || Number(lane) === laneId) delete next[Number(lane)]
      }
      if (channel) next[laneId] = channel
      return next
    })
  }

  const confirm = () => {
    const laneMap = Object.fromEntries(
      Object.entries(assignments)
        .filter(([, channel]) => channel)
        .map(([lane, channel]) => [channel, Number(lane)]),
    )
    if (saveTemplate) localStorage.setItem(TEMPLATE_KEY, JSON.stringify(laneMap))
    onConfirm({ encoding: inspection.encoding, laneMap, randomValues, preserveUnmapped })
  }

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose() }}
    >
      <section className="bms-import-dialog" role="dialog" aria-modal="true" aria-labelledby="bms-import-title">
        <header className="dialog-header">
          <div>
            <span className="dialog-format"><FileText size={14} />BMS IMPORT</span>
            <h2 id="bms-import-title">{inspection.title}</h2>
            <p>{inspection.filename} · {inspection.artist}</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} disabled={busy} title="关闭">
            <X size={17} />
          </button>
        </header>

        <div className="dialog-body">
          <div className="bms-stats" aria-label="BMS 概要">
            <span><small>BPM</small><strong>{inspection.initial_bpm}</strong></span>
            <span><small>小节</small><strong>{inspection.measure_count}</strong></span>
            <span><small>WAV</small><strong>{inspection.wav_count}</strong></span>
            <span><small>BGA</small><strong>{inspection.bmp_count}</strong></span>
            <span><small>PPQN</small><strong>{inspection.resolution}</strong></span>
            <span><small>KEY</small><strong>{inspection.playable_channels.length}</strong></span>
          </div>

          <fieldset className="dialog-section bms-resource-section">
            <legend>谱面与音频/视觉资源</legend>
            <div className="bms-resource-controls">
              <label>
                <span><FileText size={13} />谱面</span>
                <select
                  value={selectedChartPath}
                  onChange={(event) => onChartChange(event.target.value)}
                  disabled={busy || chartFiles.length < 2}
                  aria-label="BMS 谱面文件"
                >
                  {chartFiles.map((file) => {
                    const path = bmsResourcePath(file)
                    return <option key={path} value={path}>{path}</option>
                  })}
                </select>
              </label>
              <div className={`bms-resource-status ${resourcesComplete ? 'complete' : ''}`}>
                <span>
                  <Music2 size={13} /><b>KEY</b><strong>{audioResourceMatches} / {inspection.wav_count}</strong>
                  <ImageIcon size={13} /><b>BGA</b><strong>{visualResourceMatches} / {inspection.bmp_count}</strong>
                </span>
                <button type="button" className="button secondary" onClick={() => resourceInputRef.current?.click()} disabled={busy}>
                  <FolderOpen size={14} />选择资源目录
                </button>
                <input
                  ref={(node) => {
                    resourceInputRef.current = node
                    node?.setAttribute('webkitdirectory', '')
                  }}
                  type="file"
                  multiple
                  hidden
                  onChange={(event) => {
                    onResourceFiles(Array.from(event.target.files ?? []))
                    event.currentTarget.value = ''
                  }}
                />
              </div>
            </div>
          </fieldset>

          <fieldset className="dialog-section encoding-section">
            <legend>文本编码</legend>
            <select
              value={inspection.encoding}
              onChange={(event) => onEncodingChange(event.target.value)}
              disabled={busy}
              aria-label="BMS 文本编码"
            >
              {inspection.encoding_candidates.map((candidate) => (
                <option key={candidate.encoding} value={candidate.encoding}>{candidate.label}</option>
              ))}
            </select>
            {selectedCandidate?.preview && <pre>{selectedCandidate.preview}</pre>}
          </fieldset>

          <fieldset className="dialog-section lane-map-section">
            <legend>六路输入 Lane 映射</legend>
            <div className="lane-map-grid">
              {LANES.map((lane) => (
                <label key={lane.id}>
                  <span className="target-lane"><i style={{ background: lane.color }} /><b>{lane.name}</b><small>L{lane.id}</small></span>
                  <ArrowRight size={13} />
                  <select
                    value={assignments[lane.id] ?? ''}
                    onChange={(event) => assignChannel(lane.id, event.target.value)}
                    disabled={busy}
                    aria-label={`${lane.name}来源通道`}
                  >
                    <option value="">不占用正式输入</option>
                    {inspection.playable_channels.map((source) => (
                      <option
                        key={source.channel}
                        value={source.channel}
                        disabled={selectedChannels.has(source.channel) && assignments[lane.id] !== source.channel}
                      >
                        {source.label} · {source.note_count}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
            <label className="save-map-option">
              <input type="checkbox" checked={saveTemplate} onChange={(event) => setSaveTemplate(event.target.checked)} />
              保存为默认映射
            </label>
            <label className="save-map-option">
              <input type="checkbox" checked={preserveUnmapped} onChange={(event) => setPreserveUnmapped(event.target.checked)} />
              未映射通道保留为匿名 Track
            </label>
          </fieldset>

          {inspection.random_blocks.length > 0 && (
            <fieldset className="dialog-section random-section">
              <legend>RANDOM 分支</legend>
              <div>
                {inspection.random_blocks.map((block) => (
                  <label key={block.index}>
                    <span>#{block.index}</span>
                    <select
                      value={randomValues[block.index] ?? block.selected}
                      onChange={(event) => setRandomValues((current) => ({
                        ...current,
                        [block.index]: Number(event.target.value),
                      }))}
                      disabled={busy}
                    >
                      {Array.from({ length: block.maximum }, (_, index) => index + 1).map((value) => (
                        <option key={value} value={value}>{value}</option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>
            </fieldset>
          )}

          {inspection.warnings.length > 0 && (
            <div className="import-warnings">
              <div><AlertTriangle size={14} /><strong>兼容性提示 · {inspection.warnings.length}</strong></div>
              <ul>{inspection.warnings.slice(0, 6).map((warning) => <li key={warning}>{warning}</li>)}</ul>
            </div>
          )}
        </div>

        <footer className="dialog-footer">
          <span>正式输入 {selectedChannels.size} · 匿名 Track {preserveUnmapped ? anonymousCount : 0} · KEY {audioResourceMatches}/{inspection.wav_count} · BGA {visualResourceMatches}/{inspection.bmp_count}</span>
          <div>
            <button type="button" className="button secondary" onClick={onClose} disabled={busy}>取消</button>
            <button type="button" className="button primary" onClick={confirm} disabled={busy || !canImport}>
              {busy ? <LoaderCircle className="spin" size={15} /> : <ArrowRight size={15} />}
              导入到当前难度
            </button>
          </div>
        </footer>
      </section>
    </div>
  )
}
