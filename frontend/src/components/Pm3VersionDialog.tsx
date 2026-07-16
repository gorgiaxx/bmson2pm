import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FileArchive,
  FileText,
  Layers3,
  LoaderCircle,
  Music2,
  PackageCheck,
  Search,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type {
  DifficultyId,
  Pm3VersionCandidate,
  Pm3VersionEntry,
  Pm3VersionPreview,
  Pm3VersionReport,
} from '../types'

interface Pm3VersionDialogProps {
  currentProjectId: string
  currentDifficulty: DifficultyId
  onClose: () => void
  onComplete: (report: Pm3VersionReport) => void
}

interface VersionRow extends Omit<Pm3VersionEntry, 'difficulty'> {
  difficulties: DifficultyId[]
}

const MV_IDS = Array.from({ length: 20 }, (_, value) => value).filter((value) => value !== 17)

function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024).toFixed(1)} KB`
}

function initialRows(
  candidates: Pm3VersionCandidate[],
  currentProjectId: string,
  currentDifficulty: DifficultyId,
): VersionRow[] {
  const used = new Set<number>()
  let nextSongId = 211
  return candidates.map((candidate) => {
    let songId = candidate.song_id
    if (songId === null || used.has(songId)) {
      while (used.has(nextSongId) && nextSongId <= 999) nextSongId += 1
      songId = Math.min(999, nextSongId)
      nextSongId += 1
    }
    used.add(songId)
    const difficultyIds = candidate.difficulties.map((item) => item.id)
    const selectedDifficulties = candidate.project_id === currentProjectId
      ? [currentDifficulty, ...difficultyIds.filter((item) => item !== currentDifficulty)]
        .filter((item) => difficultyIds.includes(item))
      : []
    return {
      project_id: candidate.project_id,
      difficulties: selectedDifficulties,
      song_id: songId,
      slot: candidate.slot,
      mv_id: candidate.mv_id,
    }
  })
}

export function Pm3VersionDialog({
  currentProjectId,
  currentDifficulty,
  onClose,
  onComplete,
}: Pm3VersionDialogProps) {
  const [versionName, setVersionName] = useState('ver010')
  const [candidates, setCandidates] = useState<Pm3VersionCandidate[]>([])
  const [rows, setRows] = useState<VersionRow[]>([])
  const [search, setSearch] = useState('')
  const [preview, setPreview] = useState<Pm3VersionPreview | null>(null)
  const [report, setReport] = useState<Pm3VersionReport | null>(null)
  const [previewTab, setPreviewTab] = useState<'update' | 'song-list'>('update')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const selectedEntries = useMemo<Pm3VersionEntry[]>(() => rows.flatMap((row) => (
    row.difficulties.map((difficulty) => ({
      project_id: row.project_id,
      difficulty,
      song_id: row.song_id,
      slot: row.slot,
      mv_id: row.mv_id,
    }))
  )), [rows])
  const selectedSongCount = useMemo(
    () => rows.filter((row) => row.difficulties.length > 0).length,
    [rows],
  )
  const selectedKey = JSON.stringify(selectedEntries)
  const visibleCandidates = useMemo(() => {
    const value = search.trim().toLocaleLowerCase()
    if (!value) return candidates
    return candidates.filter((candidate) => (
      candidate.title.toLocaleLowerCase().includes(value)
      || candidate.artist.toLocaleLowerCase().includes(value)
      || String(candidate.song_id ?? '').includes(value)
    ))
  }, [candidates, search])

  useEffect(() => {
    let cancelled = false
    void api.pm3VersionCandidates()
      .then((items) => {
        if (cancelled) return
        setCandidates(items)
        setRows(initialRows(items, currentProjectId, currentDifficulty))
      })
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : '无法读取版本候选曲目')
      })
    return () => { cancelled = true }
  }, [currentDifficulty, currentProjectId])

  useEffect(() => {
    let cancelled = false
    setPreview(null)
    setError(null)
    if (!/^ver\d{3}$/.test(versionName) || !selectedEntries.length) return () => { cancelled = true }
    const timer = window.setTimeout(() => {
      void api.pm3VersionPreview(versionName, selectedEntries)
        .then((value) => { if (!cancelled) setPreview(value) })
        .catch((reason) => {
          if (!cancelled) setError(reason instanceof Error ? reason.message : 'PM3 多曲版本预检失败')
        })
    }, 180)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [selectedKey, versionName])

  const updateRow = (projectId: string, patch: Partial<VersionRow>) => {
    setRows((current) => current.map((row) => (
      row.project_id === projectId ? { ...row, ...patch } : row
    )))
  }

  const toggleDifficulty = (projectId: string, difficulty: DifficultyId, selected: boolean) => {
    setRows((current) => current.map((row) => {
      if (row.project_id !== projectId) return row
      const difficulties = selected
        ? [...new Set([...row.difficulties, difficulty])]
        : row.difficulties.filter((item) => item !== difficulty)
      return { ...row, difficulties }
    }))
  }

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const completed = await api.exportPm3Version(versionName, selectedEntries)
      setReport(completed)
      onComplete(completed)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'PM3 多曲版本构建失败')
    } finally {
      setBusy(false)
    }
  }

  const stats = report?.stats ?? preview?.stats
  const files = report?.files ?? preview?.files ?? []
  const warnings = report?.warnings ?? preview?.warnings ?? []

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !busy) onClose()
    }}>
      <section className="pm3-version-dialog" role="dialog" aria-modal="true" aria-labelledby="pm3-version-title">
        <header className="dialog-header">
          <div>
            <span className="dialog-format"><Layers3 size={14} />PM3 VERSION</span>
            <h2 id="pm3-version-title">多曲离线版本</h2>
            <p>{versionName} · {stats?.song_count ?? selectedSongCount} SONGS · {stats?.chart_count ?? selectedEntries.length} CHARTS</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} disabled={busy} title="关闭">
            <X size={17} />
          </button>
        </header>

        <div className="dialog-body">
          <div className="pm3-export-stats" aria-label="PM3 多曲版本概要">
            <span><small>SONGS</small><strong>{stats?.song_count ?? selectedSongCount}</strong></span>
            <span><small>CHARTS</small><strong>{stats?.chart_count ?? selectedEntries.length}</strong></span>
            <span><small>BUNDLES</small><strong>{stats?.bundles?.join(',') || '—'}</strong></span>
            <span className={preview?.valid || report ? 'verified' : ''}><small>VERIFY</small><strong>{preview?.valid || report ? 'PASS' : 'WAIT'}</strong></span>
          </div>

          {!report && (
            <fieldset className="dialog-section pm3-version-options">
              <legend>版本配置</legend>
              <div className="pm3-version-toolbar">
                <label>
                  <span><FileArchive size={13} />版本目录</span>
                  <input
                    value={versionName}
                    onChange={(event) => setVersionName(event.target.value.toLowerCase())}
                    aria-label="版本目录"
                    aria-invalid={!/^ver\d{3}$/.test(versionName)}
                    maxLength={6}
                    disabled={busy}
                  />
                </label>
                <label>
                  <span><Search size={13} />筛选曲目</span>
                  <input value={search} onChange={(event) => setSearch(event.target.value)} aria-label="筛选曲目" disabled={busy} />
                </label>
              </div>

              <div className="pm3-version-list" role="table" aria-label="版本曲目">
                <div className="pm3-version-list-head" role="row">
                  <span>曲目</span><span>难度</span><span>ID</span><span>KEY</span><span>MV</span><span>资源</span>
                </div>
                {visibleCandidates.map((candidate) => {
                  const row = rows.find((item) => item.project_id === candidate.project_id)
                  if (!row) return null
                  const enabled = row.difficulties.length > 0
                  return (
                    <div className={`pm3-version-row ${enabled ? 'selected' : ''}`} role="row" key={candidate.project_id}>
                      <label className="pm3-version-song">
                        <input
                          type="checkbox"
                          checked={enabled}
                          onChange={(event) => updateRow(candidate.project_id, {
                            difficulties: event.target.checked
                              ? candidate.difficulties.map((item) => item.id)
                              : [],
                          })}
                          disabled={busy || !candidate.difficulties.length}
                          aria-label={`选择 ${candidate.title}`}
                        />
                        <span><strong>{candidate.title}</strong><small>{candidate.artist}</small></span>
                      </label>
                      <div className="pm3-version-field pm3-version-difficulties" aria-label={`${candidate.title} 难度`}>
                        <span>难度</span>
                        <div>
                          {candidate.difficulties.map((difficulty) => (
                            <label key={difficulty.id} title={`${difficulty.label} · ${difficulty.notes} Notes`}>
                              <input
                                type="checkbox"
                                checked={row.difficulties.includes(difficulty.id)}
                                onChange={(event) => toggleDifficulty(candidate.project_id, difficulty.id, event.target.checked)}
                                disabled={busy}
                                aria-label={`${candidate.title} ${difficulty.label}`}
                              />
                              {difficulty.id.slice(0, 2).toUpperCase()}
                            </label>
                          ))}
                        </div>
                      </div>
                      <label className="pm3-version-field">
                        <span>ID</span>
                        <input
                          type="number" min="0" max="999" step="1" value={row.song_id}
                          onChange={(event) => updateRow(candidate.project_id, { song_id: Number(event.target.value) })}
                          disabled={busy || !enabled}
                          aria-label={`${candidate.title} 曲目序号`}
                        />
                      </label>
                      <label className="pm3-version-field">
                        <span>KEY</span>
                        <select
                          value={row.slot}
                          onChange={(event) => updateRow(candidate.project_id, { slot: Number(event.target.value) })}
                          disabled={busy || !enabled}
                          aria-label={`${candidate.title} Key slot`}
                        >
                          {Array.from({ length: 10 }, (_, value) => <option value={value} key={value}>{value}</option>)}
                        </select>
                      </label>
                      <label className="pm3-version-field">
                        <span>MV</span>
                        <select
                          value={row.mv_id}
                          onChange={(event) => updateRow(candidate.project_id, { mv_id: Number(event.target.value) })}
                          disabled={busy || !enabled}
                          aria-label={`${candidate.title} MV`}
                        >
                          {MV_IDS.map((value) => <option value={value} key={value}>{value}</option>)}
                        </select>
                      </label>
                      <span className={`pm3-version-ready ${candidate.audio_ready ? 'ready' : ''}`}>
                        <i />{candidate.audio_ready ? 'AUDIO' : 'MISSING'}
                      </span>
                    </div>
                  )
                })}
              </div>
            </fieldset>
          )}

          {report && (
            <section className="pm3-export-result staged">
              <CheckCircle2 size={18} />
              <span><strong>版本目录已生成</strong><small>{report.export_id}</small></span>
              <code>{report.version_name}</code>
            </section>
          )}

          <section className="pm3-artifacts pm3-version-artifacts">
            <div className="pm3-artifacts-title"><PackageCheck size={13} /><strong>版本产物</strong><span>{files.length} FILES</span></div>
            <div className="pm3-artifact-head"><span>路径</span><span>大小</span><span>MD5</span></div>
            {files.slice(0, 14).map((file) => (
              <div className="pm3-artifact-row" key={file.path}>
                <code>{file.path}</code><span>{file.pending ? '构建时' : sizeLabel(file.size)}</span><code>{file.pending ? 'PENDING' : file.md5.slice(0, 12)}</code>
              </div>
            ))}
            {files.length > 14 && <div className="pm3-artifact-more">+ {files.length - 14} FILES</div>}
            {!preview && !error && selectedEntries.length > 0 && <div className="pm3-artifact-loading"><LoaderCircle className="spin" size={15} />正在合并预检</div>}
          </section>

          {preview && !report && (
            <section className="pm3-export-preview pm3-version-preview">
              <div className="pm3-export-preview-tabs" role="tablist" aria-label="版本文件预览">
                <button type="button" role="tab" aria-selected={previewTab === 'update'} className={previewTab === 'update' ? 'active' : ''} onClick={() => setPreviewTab('update')}><FileText size={13} />update.lst</button>
                <button type="button" role="tab" aria-selected={previewTab === 'song-list'} className={previewTab === 'song-list' ? 'active' : ''} onClick={() => setPreviewTab('song-list')}><Music2 size={13} />SongList 明文</button>
              </div>
              <div className="pm3-export-text-preview">
                <header><strong>{previewTab === 'update' ? preview.previews.update_list.filename : preview.previews.song_list.filename}</strong><span>{previewTab === 'update' ? 'ASCII' : preview.previews.song_list.encoding.toUpperCase()}</span></header>
                <pre>{previewTab === 'update' ? preview.previews.update_list.text : preview.previews.song_list.text}</pre>
              </div>
            </section>
          )}

          {warnings.length > 0 && (
            <section className="pm3-export-warnings">
              <div><AlertTriangle size={14} /><strong>{warnings.length} 条版本提示</strong></div>
              <ul>{warnings.slice(0, 8).map((warning) => <li key={warning}>{warning}</li>)}</ul>
            </section>
          )}
          {error && <div className="pm3-export-error"><AlertTriangle size={14} /><span>{error}</span></div>}
          {!error && !selectedEntries.length && <div className="pm3-export-error"><AlertTriangle size={14} /><span>至少选择一首包含谱面的曲目</span></div>}
        </div>

        <footer className="dialog-footer">
          <span>verNNN · shared SquashFS · no host write</span>
          <div>
            {report && <button type="button" className="button secondary" onClick={() => void api.downloadPm3Version(report.export_id, report.version_name)}><Download size={14} />下载 ZIP</button>}
            {!report && <button type="button" className="button secondary" onClick={onClose} disabled={busy}>取消</button>}
            {!report && <button type="button" className="button primary" onClick={() => void submit()} disabled={busy || !preview?.valid || Boolean(error)}>
              {busy ? <LoaderCircle className="spin" size={14} /> : <Layers3 size={14} />}
              构建版本目录
            </button>}
            {report && <button type="button" className="button primary" onClick={onClose}>完成</button>}
          </div>
        </footer>
      </section>
    </div>
  )
}
