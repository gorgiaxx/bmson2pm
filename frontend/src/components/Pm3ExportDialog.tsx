import {
  AlertTriangle,
  Binary,
  CheckCircle2,
  Download,
  FileArchive,
  FileText,
  KeyRound,
  ListMusic,
  ListOrdered,
  LoaderCircle,
  PackageCheck,
  RotateCcw,
  ShieldCheck,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type {
  DifficultyId,
  Pm3ExportPreview,
  Pm3ExportReport,
  Pm3ExportTarget,
  SongProject,
} from '../types'

interface Pm3ExportDialogProps {
  project: SongProject
  difficulty: DifficultyId
  onClose: () => void
  onComplete: (report: Pm3ExportReport) => void
}

function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  return `${(bytes / 1024).toFixed(1)} KB`
}

function initialSongId(project: SongProject): string {
  const match = project.metadata.game_song_id?.match(/(\d{1,3})/)
    ?? project.metadata.source_name?.match(/p(\d{1,3})/i)
  return match ? String(Math.min(999, Number(match[1]))) : '0'
}

type PreviewTab = 'chart' | 'update_list' | 'song_list'

export function Pm3ExportDialog({ project, difficulty, onClose, onComplete }: Pm3ExportDialogProps) {
  const sourceSlot = project.game_specific_data.pm3_slot
  const [slot, setSlot] = useState(typeof sourceSlot === 'number' && sourceSlot >= 0 && sourceSlot <= 9 ? sourceSlot : 0)
  const [songIdInput, setSongIdInput] = useState(() => initialSongId(project))
  const [targets, setTargets] = useState<Pm3ExportTarget[]>([])
  const [targetId, setTargetId] = useState('staging')
  const [includeSongList, setIncludeSongList] = useState(false)
  const [preview, setPreview] = useState<Pm3ExportPreview | null>(null)
  const [report, setReport] = useState<Pm3ExportReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [previewTab, setPreviewTab] = useState<PreviewTab>('chart')
  const songId = /^\d{1,3}$/.test(songIdInput) && Number(songIdInput) <= 999
    ? Number(songIdInput)
    : null
  const currentPreview = preview?.song_id === songId ? preview : null
  const selectedTarget = useMemo(
    () => targets.find((target) => target.id === targetId),
    [targetId, targets],
  )

  useEffect(() => {
    let cancelled = false
    void api.pm3ExportTargets()
      .then((items) => { if (!cancelled) setTargets(items) })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : '无法读取导出目标') })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    let cancelled = false
    setPreview(null)
    setError(null)
    if (songId === null) return () => { cancelled = true }
    const timer = window.setTimeout(() => {
      void api.pm3ExportPreview(project.id, difficulty, songId, slot, includeSongList)
        .then((value) => { if (!cancelled) setPreview(value) })
        .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : 'PM3 预检失败') })
    }, 120)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [difficulty, includeSongList, project.id, slot, songId])

  useEffect(() => {
    if (!includeSongList && previewTab === 'song_list') setPreviewTab('chart')
  }, [includeSongList, previewTab])

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      if (songId === null) return
      const completed = await api.exportPm3(
        project.id, difficulty, targetId, songId, slot, includeSongList,
      )
      setReport(completed)
      onComplete(completed)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'PM3 导出失败')
    } finally {
      setBusy(false)
    }
  }

  const rollback = async () => {
    if (!report) return
    setBusy(true)
    setError(null)
    try {
      const rolledBack = await api.rollbackPm3(report.export_id)
      setReport(rolledBack)
      onComplete(rolledBack)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'PM3 回滚失败')
    } finally {
      setBusy(false)
    }
  }

  const stats = report?.stats ?? currentPreview?.stats
  const files = report?.files ?? currentPreview?.files ?? []
  const warnings = report?.warnings ?? currentPreview?.warnings ?? []
  const verified = report?.round_trip.passed ?? currentPreview?.stats.round_trip_verified === true

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !busy) onClose()
    }}>
      <section className="pm3-export-dialog" role="dialog" aria-modal="true" aria-labelledby="pm3-export-title">
        <header className="dialog-header">
          <div>
            <span className="dialog-format"><ShieldCheck size={14} />PM3 DEPLOY</span>
            <h2 id="pm3-export-title">{project.metadata.title}</h2>
            <p>{project.difficulties[difficulty].display_name} · {currentPreview?.filename ?? `p${songId === null ? '---' : String(songId).padStart(3, '0')}_${difficulty}.enc`}</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} disabled={busy} title="关闭">
            <X size={17} />
          </button>
        </header>

        <div className="dialog-body">
          <div className="pm3-export-stats" aria-label="PM3 导出概要">
            <span><small>SLOT</small><strong>{report?.slot ?? preview?.slot ?? slot}</strong></span>
            <span><small>PLAYER</small><strong>{String(stats?.note_objects ?? '—')}</strong></span>
            <span><small>EVENT</small><strong>{String(stats?.event_count ?? '—')}</strong></span>
            <span className={verified ? 'verified' : ''}><small>VERIFY</small><strong>{verified ? 'PASS' : 'WAIT'}</strong></span>
          </div>

          {!report && (
            <fieldset className="dialog-section pm3-export-options">
              <legend>发布配置</legend>
              <div className="pm3-export-fields">
                <label>
                  <span><PackageCheck size={13} />目标</span>
                  <select value={targetId} onChange={(event) => setTargetId(event.target.value)} disabled={busy}>
                    {targets.map((target) => <option key={target.id} value={target.id}>{target.label}</option>)}
                  </select>
                </label>
                <label>
                  <span><ListOrdered size={13} />曲目序号</span>
                  <input
                    type="number"
                    min="0"
                    max="999"
                    step="1"
                    value={songIdInput}
                    onChange={(event) => setSongIdInput(event.target.value)}
                    aria-invalid={songId === null}
                    disabled={busy}
                  />
                </label>
                <label>
                  <span title="由谱面加密 header 选择，与曲目序号无固定关系"><KeyRound size={13} />Key slot</span>
                  <select value={slot} onChange={(event) => setSlot(Number(event.target.value))} disabled={busy}>
                    {Array.from({ length: 10 }, (_, value) => <option key={value} value={value}>Slot {value}</option>)}
                  </select>
                </label>
              </div>
              <label className="pm3-songlist-option">
                <input type="checkbox" checked={includeSongList} onChange={(event) => setIncludeSongList(event.target.checked)} disabled={busy} />
                <span>同时重建 SongList.enc</span>
              </label>
            </fieldset>
          )}

          {report && (
            <section className={`pm3-export-result ${report.status}`}>
              <CheckCircle2 size={18} />
              <span><strong>{report.status === 'rolled_back' ? '已回滚' : report.status === 'published' ? '已发布' : '安全包已生成'}</strong><small>{report.export_id}</small></span>
              <code>{report.target.path}</code>
            </section>
          )}

          <section className="pm3-artifacts">
            <div className="pm3-artifacts-title"><FileArchive size={13} /><strong>产物</strong><span>{files.length} FILES</span></div>
            <div className="pm3-artifact-head"><span>路径</span><span>大小</span><span>MD5</span></div>
            {files.map((file) => (
              <div className="pm3-artifact-row" key={file.path}>
                <code>{file.path}</code><span>{sizeLabel(file.size)}</span><code>{file.md5.slice(0, 12)}</code>
              </div>
            ))}
            {!currentPreview && !error && songId !== null && <div className="pm3-artifact-loading"><LoaderCircle className="spin" size={15} />正在重建与验证</div>}
          </section>

          {currentPreview && (
            <ExportPreview
              preview={currentPreview}
              tab={previewTab}
              onTab={setPreviewTab}
            />
          )}

          {warnings.length > 0 && (
            <section className="pm3-export-warnings">
              <div><AlertTriangle size={14} /><strong>{warnings.length} 条导出提示</strong></div>
              <ul>{warnings.slice(0, 8).map((warning) => <li key={warning}>{warning}</li>)}</ul>
            </section>
          )}
          {(error || songId === null) && <div className="pm3-export-error"><AlertTriangle size={14} /><span>{error ?? '曲目序号必须是 0 到 999 的整数'}</span></div>}
        </div>

        <footer className="dialog-footer">
          <span>{selectedTarget?.kind === 'deployment' ? '备份 · 原子替换 · 写后校验' : 'rewrite overlay · ZIP · export report'}</span>
          <div>
            {report && <button type="button" className="button secondary" onClick={() => void api.downloadPm3(report.export_id)}><Download size={14} />下载 ZIP</button>}
            {report?.rollback_available && <button type="button" className="button secondary danger" onClick={() => void rollback()} disabled={busy}><RotateCcw size={14} />回滚</button>}
            {!report && <button type="button" className="button secondary" onClick={onClose} disabled={busy}>取消</button>}
            {!report && <button type="button" className="button primary" onClick={() => void submit()} disabled={busy || !currentPreview || !!error || songId === null || !targets.length}>
              {busy ? <LoaderCircle className="spin" size={14} /> : <PackageCheck size={14} />}
              {selectedTarget?.kind === 'deployment' ? '备份并发布' : '生成安全包'}
            </button>}
            {report && <button type="button" className="button primary" onClick={onClose}>完成</button>}
          </div>
        </footer>
      </section>
    </div>
  )
}

function ExportPreview({
  preview,
  tab,
  onTab,
}: {
  preview: Pm3ExportPreview
  tab: PreviewTab
  onTab: (tab: PreviewTab) => void
}) {
  const chart = preview.previews.chart
  const songList = preview.previews.song_list
  return (
    <section className="pm3-export-preview">
      <div className="pm3-export-preview-tabs" role="tablist" aria-label="PM3 文件预览">
        <button type="button" role="tab" aria-selected={tab === 'chart'} className={tab === 'chart' ? 'active' : ''} onClick={() => onTab('chart')}><Binary size={13} />谱面明文</button>
        <button type="button" role="tab" aria-selected={tab === 'update_list'} className={tab === 'update_list' ? 'active' : ''} onClick={() => onTab('update_list')}><FileText size={13} />update.lst</button>
        <button type="button" role="tab" aria-selected={tab === 'song_list'} className={tab === 'song_list' ? 'active' : ''} onClick={() => onTab('song_list')} disabled={!songList} title={songList ? '预览加密前 SongList' : '启用 SongList 重建后可预览'}><ListMusic size={13} />SongList 明文</button>
      </div>

      {tab === 'chart' && (
        <div className="pm3-chart-inspector pm3-export-chart-inspector">
          <header>
            <span className="chart-format"><Binary size={13} />PM3 / SLOT {chart.slot}</span>
            <h3>{chart.filename}</h3>
            <p>{chart.path}</p>
          </header>
          <div className="pm3-chart-metrics">
            <span><small>事件</small><strong>{chart.playable_events}</strong></span>
            <span><small>音符对象</small><strong>{chart.note_objects}</strong></span>
            <span><small>长音</small><strong>{chart.hold_notes}</strong></span>
            <span><small>WAV</small><strong>{chart.wav_count}</strong></span>
          </div>
          <dl className="pm3-chart-details">
            <div><dt>容器</dt><dd>ENC</dd></div>
            <div><dt>Header</dt><dd>{chart.header}</dd></div>
            <div><dt>明文</dt><dd>{sizeLabel(chart.plain_length)} · {chart.encoding.toUpperCase()}</dd></div>
            <div><dt>Track</dt><dd>{chart.track_ids.join(', ')}</dd></div>
          </dl>
          <pre className="pm3-chart-preview">{chart.text_preview}</pre>
        </div>
      )}
      {tab === 'update_list' && <TextPreview preview={preview.previews.update_list} />}
      {tab === 'song_list' && songList && <TextPreview preview={songList} />}
    </section>
  )
}

function TextPreview({ preview }: { preview: { filename: string; encoding: string; text: string } }) {
  return (
    <div className="pm3-export-text-preview">
      <header><strong>{preview.filename}</strong><span>{preview.encoding.toUpperCase()}</span></header>
      <pre>{preview.text}</pre>
    </div>
  )
}
