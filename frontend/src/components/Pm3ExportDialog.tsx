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
  Music2,
  PackageCheck,
  RotateCcw,
  ShieldCheck,
  Upload,
  Video,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type {
  DifficultyId,
  Pm3ExportPreview,
  Pm3ExportReport,
  Pm3ExportTarget,
  Pm3ResourceProfile,
  SongProject,
} from '../types'
import {
  PM3_RESERVED_SONG_IDS,
  pm3CompatibleReservedIds,
  pm3ReservedSlot,
} from '../pm3Reservations'
import { Pm3MvPreview } from './Pm3MvPreview'

interface Pm3ExportDialogProps {
  project: SongProject
  difficulty: DifficultyId
  onClose: () => void
  onComplete: (report: Pm3ExportReport) => void
  onProjectChange: (project: SongProject) => void
}

const PM3_MV_IDS = Array.from({ length: 20 }, (_, value) => value).filter((value) => value !== 17)
const PM3_MUSIC_STYLES = [
  { value: 0, label: '流行' },
  { value: 1, label: '古典／童谣' },
  { value: 2, label: '原创／其他' },
]

function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024).toFixed(1)} KB`
}

function initialSongId(project: SongProject, difficulty: DifficultyId): number {
  const match = project.metadata.game_song_id?.match(/(\d{1,3})/)
    ?? project.metadata.source_name?.match(/p(\d{1,3})/i)
  const configured = project.game_specific_data.pm3_song_id
  const candidate = typeof configured === 'number'
    ? configured
    : match ? Number(match[1]) : null
  const compatible = pm3CompatibleReservedIds([difficulty])
  return candidate !== null && compatible.includes(candidate)
    ? candidate
    : compatible[0] ?? PM3_RESERVED_SONG_IDS[0]
}

type PreviewTab = 'chart' | 'update_list' | 'song_list'

function pm3AudioConfig(project: SongProject): Record<string, unknown> {
  const packageConfig = project.game_specific_data.pm3_package
  if (!packageConfig || typeof packageConfig !== 'object' || Array.isArray(packageConfig)) return {}
  const audio = (packageConfig as Record<string, unknown>).audio
  return audio && typeof audio === 'object' && !Array.isArray(audio)
    ? audio as Record<string, unknown>
    : {}
}

function pm3MvConfig(project: SongProject): Record<string, unknown> {
  const packageConfig = project.game_specific_data.pm3_package
  if (!packageConfig || typeof packageConfig !== 'object' || Array.isArray(packageConfig)) return {}
  const mv = (packageConfig as Record<string, unknown>).mv
  return mv && typeof mv === 'object' && !Array.isArray(mv)
    ? mv as Record<string, unknown>
    : {}
}

export function Pm3ExportDialog({
  project,
  difficulty,
  onClose,
  onComplete,
  onProjectChange,
}: Pm3ExportDialogProps) {
  const initialAudio = pm3AudioConfig(project)
  const initialMv = pm3MvConfig(project)
  const configuredMvId = typeof initialMv.id === 'number'
    && initialMv.id >= 20 && initialMv.id <= 99
    ? initialMv.id
    : null
  const projectMvId = project.mv_configuration.pm3_mv_id
  const audioInputRef = useRef<HTMLInputElement>(null)
  const mvInputRef = useRef<HTMLInputElement>(null)
  const compatibleSongIds = useMemo(
    () => pm3CompatibleReservedIds([difficulty]),
    [difficulty],
  )
  const [songId, setSongId] = useState(() => initialSongId(project, difficulty))
  const slot = pm3ReservedSlot(songId, difficulty) ?? 0
  const [targets, setTargets] = useState<Pm3ExportTarget[]>([])
  const [targetId, setTargetId] = useState('staging')
  const [includeSongList, setIncludeSongList] = useState(false)
  const [includeResources, setIncludeResources] = useState(false)
  const [musicStyle, setMusicStyle] = useState(() => {
    const configured = project.game_specific_data.pm3_music_style
    return typeof configured === 'number' && configured >= 0 && configured <= 2
      ? configured
      : 0
  })
  const [guestAvailable, setGuestAvailable] = useState(
    project.game_specific_data.pm3_guest_available !== false,
  )
  const [resourceProfile, setResourceProfile] = useState<Pm3ResourceProfile>('extracted-media-overlay')
  const [mvId, setMvId] = useState(
    typeof projectMvId === 'number'
      && (PM3_MV_IDS.includes(projectMvId) || (projectMvId >= 20 && projectMvId <= 99))
      ? projectMvId
      : 0,
  )
  const [audioFile, setAudioFile] = useState<File | null>(null)
  const [previewStart, setPreviewStart] = useState(
    typeof initialAudio.preview_start === 'number' ? initialAudio.preview_start : project.metadata.preview_time,
  )
  const [previewDuration, setPreviewDuration] = useState(
    typeof initialAudio.preview_duration === 'number' ? initialAudio.preview_duration : 12,
  )
  const [audioRevision, setAudioRevision] = useState(0)
  const [mvFile, setMvFile] = useState<File | null>(null)
  const [customMvId, setCustomMvId] = useState(configuredMvId ?? 20)
  const [mvRevision, setMvRevision] = useState(0)
  const [preview, setPreview] = useState<Pm3ExportPreview | null>(null)
  const [report, setReport] = useState<Pm3ExportReport | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [previewTab, setPreviewTab] = useState<PreviewTab>('chart')
  const currentPreview = preview?.song_id === songId
    && preview.include_resources === includeResources
    && preview.music_style === musicStyle
    && preview.guest_available === guestAvailable
    && preview.mv_id === mvId
    && preview.resource_profile === resourceProfile
    ? preview
    : null
  const selectedTarget = useMemo(
    () => targets.find((target) => target.id === targetId),
    [targetId, targets],
  )

  useEffect(() => {
    if (!compatibleSongIds.includes(songId)) {
      setSongId(compatibleSongIds[0] ?? PM3_RESERVED_SONG_IDS[0])
    }
  }, [compatibleSongIds, songId])

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
    const timer = window.setTimeout(() => {
      void api.pm3ExportPreview(
        project.id, difficulty, songId, slot, includeSongList, includeResources,
        musicStyle, guestAvailable, mvId,
        resourceProfile,
      )
        .then((value) => { if (!cancelled) setPreview(value) })
        .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : 'PM3 预检失败') })
    }, 120)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [audioRevision, difficulty, guestAvailable, includeResources, includeSongList, musicStyle, mvId, mvRevision, project.id, resourceProfile, slot, songId])

  useEffect(() => {
    if (!includeSongList && previewTab === 'song_list') setPreviewTab('chart')
  }, [includeSongList, previewTab])

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const completed = await api.exportPm3(
        project.id, difficulty, targetId, songId, slot, includeSongList, includeResources,
        musicStyle, guestAvailable, mvId,
        resourceProfile,
      )
      setReport(completed)
      onProjectChange({
        ...project,
        metadata: { ...project.metadata, game_song_id: `p${String(songId).padStart(3, '0')}` },
        game_specific_data: {
          ...project.game_specific_data,
          pm3_song_id: songId,
          pm3_slot: slot,
          pm3_music_style: musicStyle,
          pm3_guest_available: guestAvailable,
        },
      })
      onComplete(completed)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'PM3 导出失败')
    } finally {
      setBusy(false)
    }
  }

  const prepareAudio = async () => {
    if (!audioFile) return
    setBusy(true)
    setError(null)
    try {
      await api.saveProject(project)
      const updated = await api.preparePm3Audio(
        project.id, audioFile, previewStart, previewDuration,
      )
      onProjectChange(updated)
      setAudioFile(null)
      setAudioRevision((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'PM3 音频资源生成失败')
    } finally {
      setBusy(false)
    }
  }

  const prepareMv = async () => {
    if (!mvFile || customMvId < 20 || customMvId > 99) return
    setBusy(true)
    setError(null)
    try {
      await api.saveProject(project)
      const updated = await api.preparePm3Mv(project.id, mvFile, customMvId)
      onProjectChange(updated)
      setMvFile(null)
      setMvId(customMvId)
      setMvRevision((value) => value + 1)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'PM3 MV 校验或保存失败')
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
  const resourcePackage = report?.resource_package ?? currentPreview?.resource_package
  const selectableMvIds = configuredMvId !== null && !PM3_MV_IDS.includes(configuredMvId)
    ? [...PM3_MV_IDS, configuredMvId]
    : PM3_MV_IDS

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !busy) onClose()
    }}>
      <section className="pm3-export-dialog" role="dialog" aria-modal="true" aria-labelledby="pm3-export-title">
        <header className="dialog-header">
          <div>
            <span className="dialog-format"><ShieldCheck size={14} />PM3 DEPLOY</span>
            <h2 id="pm3-export-title">{project.metadata.title}</h2>
            <p>{project.difficulties[difficulty].display_name} · {currentPreview?.filename ?? `p${String(songId).padStart(3, '0')}_${difficulty}.enc`}</p>
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
            <>
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
                    <select value={songId} onChange={(event) => setSongId(Number(event.target.value))} disabled={busy} aria-label="曲目序号">
                      {compatibleSongIds.map((value) => <option value={value} key={value}>ID {String(value).padStart(3, '0')}</option>)}
                    </select>
                  </label>
                  <label>
                    <span title="由预留曲目 ID 与当前难度的原厂 cut table 自动确定"><KeyRound size={13} />Key slot</span>
                    <select value={slot} disabled aria-label="Key slot">
                      <option value={slot}>Slot {slot} · AUTO</option>
                    </select>
                  </label>
                  <label>
                    <span><ListMusic size={13} />音乐分类</span>
                    <select
                      value={musicStyle}
                      onChange={(event) => setMusicStyle(Number(event.target.value))}
                      disabled={busy}
                      aria-label="音乐分类"
                    >
                      {PM3_MUSIC_STYLES.map((style) => (
                        <option value={style.value} key={style.value}>{style.label}</option>
                      ))}
                    </select>
                  </label>
                </div>
                <div className="pm3-export-toggles">
                  <label>
                    <input type="checkbox" checked={includeSongList} onChange={(event) => setIncludeSongList(event.target.checked)} disabled={busy} />
                    <span>重建 SongList.enc</span>
                  </label>
                  <label>
                    <input type="checkbox" checked={includeResources} onChange={(event) => setIncludeResources(event.target.checked)} disabled={busy} />
                    <span>{resourceProfile === 'squashfs-ota'
                      ? '包含音乐、试听与 SquashFS ROM'
                      : '包含音乐、试听与 MV 清单'}</span>
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={guestAvailable}
                      onChange={(event) => setGuestAvailable(event.target.checked)}
                      disabled={busy}
                    />
                    <span>游客直接开放</span>
                  </label>
                </div>
              </fieldset>

              <fieldset className="dialog-section pm3-resource-options">
                <legend>歌曲资源</legend>
                <div className="pm3-resource-mode" role="group" aria-label="资源包格式">
                  <button
                    type="button"
                    className={resourceProfile === 'extracted-media-overlay' ? 'active' : ''}
                    aria-pressed={resourceProfile === 'extracted-media-overlay'}
                    onClick={() => setResourceProfile('extracted-media-overlay')}
                    disabled={busy}
                  >
                    <FileText size={12} />文件覆盖
                  </button>
                  <button
                    type="button"
                    className={resourceProfile === 'squashfs-ota' ? 'active' : ''}
                    aria-pressed={resourceProfile === 'squashfs-ota'}
                    onClick={() => setResourceProfile('squashfs-ota')}
                    disabled={busy}
                  >
                    <FileArchive size={12} />离线 ROM
                  </button>
                </div>
                <div className="pm3-resource-fields">
                  <label className="pm3-audio-source">
                    <span><Music2 size={13} />主音乐</span>
                    <button type="button" className="button secondary" onClick={() => audioInputRef.current?.click()} disabled={busy}>
                      <Upload size={13} />
                      <span>{audioFile?.name ?? resourcePackage?.audio.source_name ?? '选择音频'}</span>
                    </button>
                  </label>
                  <label>
                    <span>试听起点</span>
                    <div className="pm3-number-unit"><input type="number" min="0" step="0.1" value={previewStart} onChange={(event) => setPreviewStart(Number(event.target.value))} disabled={busy} /><i>s</i></div>
                  </label>
                  <label>
                    <span>试听长度</span>
                    <div className="pm3-number-unit"><input type="number" min="1" max="60" step="0.1" value={previewDuration} onChange={(event) => setPreviewDuration(Number(event.target.value))} disabled={busy} /><i>s</i></div>
                  </label>
                  <label>
                    <span><Video size={13} />MV 预设</span>
                    <select value={mvId} onChange={(event) => setMvId(Number(event.target.value))} disabled={busy}>
                      {selectableMvIds.map((value) => (
                        <option key={value} value={value}>
                          MV {value}{value >= 20 ? ' · 自定义' : ''}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button type="button" className="button primary pm3-prepare-audio" onClick={() => void prepareAudio()} disabled={busy || !audioFile || !Number.isFinite(previewStart) || !Number.isFinite(previewDuration)}>
                    {busy ? <LoaderCircle className="spin" size={13} /> : <Music2 size={13} />}
                    生成音频资源
                  </button>
                </div>
                <div className="pm3-mv-upload">
                  <label>
                    <span><Video size={13} />自定义 MV</span>
                    <button type="button" className="button secondary" onClick={() => mvInputRef.current?.click()} disabled={busy}>
                      <Upload size={13} />
                      <span>{mvFile?.name ?? (typeof initialMv.source_name === 'string' ? initialMv.source_name : '选择 SWF')}</span>
                    </button>
                  </label>
                  <label>
                    <span>MV ID</span>
                    <input
                      type="number"
                      min="20"
                      max="99"
                      step="1"
                      value={customMvId}
                      onChange={(event) => setCustomMvId(Number(event.target.value))}
                      disabled={busy}
                    />
                  </label>
                  <button
                    type="button"
                    className="button secondary"
                    onClick={() => void prepareMv()}
                    disabled={busy || !mvFile || customMvId < 20 || customMvId > 99}
                  >
                    {busy ? <LoaderCircle className="spin" size={13} /> : <Video size={13} />}
                    校验并保存 MV
                  </button>
                </div>
                <input
                  ref={audioInputRef}
                  type="file"
                  accept=".wav,.ogg,.mp3,.flac,.aif,.aiff,audio/*"
                  hidden
                  onChange={(event) => {
                    setAudioFile(event.target.files?.[0] ?? null)
                    event.currentTarget.value = ''
                  }}
                />
                <input
                  ref={mvInputRef}
                  type="file"
                  accept=".swf,application/x-shockwave-flash"
                  hidden
                  onChange={(event) => {
                    setMvFile(event.target.files?.[0] ?? null)
                    event.currentTarget.value = ''
                  }}
                />
                {resourcePackage && (
                  <>
                    <div className={`pm3-resource-status ${resourcePackage.rom ? 'has-rom' : ''}`} aria-label="PM3 资源状态">
                      <span className={resourcePackage.audio.background.available ? 'ready' : ''}>
                        <i />主音乐<code>{resourcePackage.audio.background.output_path}</code>
                      </span>
                      <span className={resourcePackage.audio.preview.available ? 'ready' : ''}>
                        <i />试听<code>{resourcePackage.audio.preview.output_path}</code>
                      </span>
                      <span className={resourcePackage.mv.available ? 'ready' : ''}>
                        <i />MV
                        <code>{resourcePackage.mv.custom
                          ? resourcePackage.mv.output_path ?? resourcePackage.mv.mapping
                          : resourcePackage.mv.mapping}</code>
                      </span>
                      {resourcePackage.rom && (
                        <span className={resourcePackage.rom.available ? 'ready' : ''}>
                          <i />ROM
                          <code>{resourcePackage.rom.available
                            ? `BG/PRE ${resourcePackage.rom.bundle} · LUA`
                            : resourcePackage.rom.missing.join('、')}</code>
                        </span>
                      )}
                    </div>
                    <Pm3MvPreview
                      projectId={project.id}
                      mvId={resourcePackage.mv.id}
                      available={resourcePackage.mv.available}
                    />
                  </>
                )}
              </fieldset>
            </>
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
                <code>{file.path}</code><span>{file.pending ? '构建时' : sizeLabel(file.size)}</span><code>{file.pending ? 'PENDING' : file.md5.slice(0, 12)}</code>
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
          <span>{selectedTarget?.kind === 'deployment'
            ? '备份 · 原子替换 · 写后校验'
            : resourceProfile === 'squashfs-ota'
              ? 'PowerOn update.lst · SquashFS 4.0 · ZIP'
              : 'rewrite overlay · ZIP · export report'}</span>
          <div>
            {report && <button type="button" className="button secondary" onClick={() => void api.downloadPm3(report.export_id)}><Download size={14} />下载 ZIP</button>}
            {report?.rollback_available && <button type="button" className="button secondary danger" onClick={() => void rollback()} disabled={busy}><RotateCcw size={14} />回滚</button>}
            {!report && <button type="button" className="button secondary" onClick={onClose} disabled={busy}>取消</button>}
            {!report && <button type="button" className="button primary" onClick={() => void submit()} disabled={busy || !currentPreview?.valid || !!error || songId === null || !targets.length}>
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
