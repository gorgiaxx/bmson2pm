import {
  ArrowLeft,
  Binary,
  Check,
  ChevronRight,
  File,
  FileText,
  Folder,
  FolderOpen,
  GitCompareArrows,
  Import,
  Info,
  ListMusic,
  LoaderCircle,
  Microscope,
  PanelLeft,
  PanelRight,
  RefreshCw,
  Search,
  ShieldCheck,
  TriangleAlert,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { Pm3OtaAuditView } from './Pm3OtaAuditView'
import type {
  DifficultyId,
  Pm3Catalog,
  Pm3CatalogRecord,
  Pm3ChartInspection,
  Pm3DiffResult,
  Pm3DirectoryListing,
  Pm3FileEntry,
  Pm3FileInspection,
  Pm3FileRef,
  Pm3Root,
  SongProject,
} from '../types'

type ResearchTab = 'catalog' | 'files' | 'compare' | 'audit'
type ViewerTab = 'hex' | 'text' | 'chart'

interface Pm3ResearchDialogProps {
  difficulty: DifficultyId
  onClose: () => void
  onImported: (project: SongProject, sourceName: string) => void
}

const DIFFICULTY_LABELS: Record<DifficultyId, string> = {
  easy: '初级', normal: '中级', hard: '高级', special: '超高级', master: '大师级',
}

function formatBytes(value: number | null | undefined): string {
  if (value == null) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(value < 10240 ? 1 : 0)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function sourceLabel(ref: Pm3FileRef | null): string {
  return ref ? `${ref.root_id}:${ref.path}` : '未选择'
}

export function Pm3ResearchDialog({ difficulty, onClose, onImported }: Pm3ResearchDialogProps) {
  const [tab, setTab] = useState<ResearchTab>('catalog')
  const [viewerTab, setViewerTab] = useState<ViewerTab>('hex')
  const [roots, setRoots] = useState<Pm3Root[]>([])
  const [rootId, setRootId] = useState('game')
  const [listing, setListing] = useState<Pm3DirectoryListing | null>(null)
  const [selectedEntry, setSelectedEntry] = useState<Pm3FileEntry | null>(null)
  const [fileInspection, setFileInspection] = useState<Pm3FileInspection | null>(null)
  const [chartInspection, setChartInspection] = useState<Pm3ChartInspection | null>(null)
  const [catalog, setCatalog] = useState<Pm3Catalog | null>(null)
  const [search, setSearch] = useState('')
  const [catalogOffset, setCatalogOffset] = useState(0)
  const [filePage, setFilePage] = useState(0)
  const [selectedRecord, setSelectedRecord] = useState<Pm3CatalogRecord | null>(null)
  const [compareLeft, setCompareLeft] = useState<Pm3FileRef | null>(null)
  const [compareRight, setCompareRight] = useState<Pm3FileRef | null>(null)
  const [diff, setDiff] = useState<Pm3DiffResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState('')

  const loadDirectory = useCallback(async (nextRoot: string, path = '') => {
    setLoading(true)
    setError('')
    try {
      const next = await api.pm3Tree(nextRoot, path)
      setRootId(nextRoot)
      setListing(next)
      setFilePage(0)
      setSelectedEntry(null)
      setFileInspection(null)
      setChartInspection(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '目录读取失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    const start = async () => {
      setLoading(true)
      try {
        const availableRoots = await api.pm3Roots()
        if (cancelled) return
        setRoots(availableRoots)
        const initialRoot = availableRoots.find((root) => root.id === 'game' && root.available)
          ?? availableRoots.find((root) => root.available)
        if (initialRoot) await loadDirectory(initialRoot.id)
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : 'PM3 工作区初始化失败')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void start()
    return () => { cancelled = true }
  }, [loadDirectory])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      api.pm3Catalog(search, catalogOffset, 100)
        .then((next) => setCatalog(next))
        .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : '曲目目录读取失败'))
    }, 220)
    return () => window.clearTimeout(timer)
  }, [catalogOffset, search])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !importing) onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [importing, onClose])

  useEffect(() => {
    if (!compareLeft || !compareRight) {
      setDiff(null)
      return
    }
    let cancelled = false
    setLoading(true)
    api.pm3Diff(compareLeft, compareRight)
      .then((result) => { if (!cancelled) setDiff(result) })
      .catch((reason: unknown) => { if (!cancelled) setError(reason instanceof Error ? reason.message : '文件比较失败') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [compareLeft, compareRight])

  const breadcrumbs = useMemo(() => {
    const parts = listing?.path.split('/').filter(Boolean) ?? []
    return [
      { label: roots.find((root) => root.id === rootId)?.label ?? rootId, path: '' },
      ...parts.map((part, index) => ({ label: part, path: parts.slice(0, index + 1).join('/') })),
    ]
  }, [listing?.path, rootId, roots])

  const openEntry = async (entry: Pm3FileEntry) => {
    if (entry.type === 'directory') {
      await loadDirectory(rootId, entry.path)
      return
    }
    if (entry.type !== 'file') return
    setSelectedRecord(null)
    setSelectedEntry(entry)
    setLoading(true)
    setError('')
    try {
      const [file, chart] = await Promise.all([
        api.pm3File(rootId, entry.path),
        entry.role === 'chart' ? api.pm3Chart(rootId, entry.path).catch(() => null) : Promise.resolve(null),
      ])
      setFileInspection(file)
      setChartInspection(chart)
      setViewerTab(chart ? 'chart' : (file.text ? 'text' : 'hex'))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '文件读取失败')
    } finally {
      setLoading(false)
    }
  }

  const chooseRecord = async (record: Pm3CatalogRecord) => {
    setSelectedEntry(null)
    setFileInspection(null)
    setSelectedRecord(record)
    setLoading(true)
    setError('')
    try {
      setChartInspection(await api.pm3Chart(record.root_id, record.path))
    } catch (reason) {
      setChartInspection(null)
      setError(reason instanceof Error ? reason.message : '谱面解密预检失败')
    } finally {
      setLoading(false)
    }
  }

  const inspectPage = async (offset: number) => {
    if (!fileInspection) return
    setLoading(true)
    try {
      setFileInspection(await api.pm3File(fileInspection.root_id, fileInspection.path, offset))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '文件分页读取失败')
    } finally {
      setLoading(false)
    }
  }

  const currentRef = useMemo<Pm3FileRef | null>(() => {
    if (!selectedEntry) return null
    return { root_id: rootId, path: selectedEntry.path, name: selectedEntry.name }
  }, [rootId, selectedEntry])

  const visibleFileEntries = useMemo(
    () => listing?.entries.slice(filePage * 200, (filePage + 1) * 200) ?? [],
    [filePage, listing?.entries],
  )

  const selectedFileChart = selectedEntry
    && chartInspection?.root_id === rootId
    && chartInspection.path === selectedEntry.path
    ? chartInspection
    : null

  const importSource = tab === 'catalog' && selectedRecord
    ? { root_id: selectedRecord.root_id, path: selectedRecord.path, name: selectedRecord.filename, difficulty: selectedRecord.difficulty }
    : tab === 'files' && selectedFileChart
      ? { root_id: selectedFileChart.root_id, path: selectedFileChart.path, name: selectedFileChart.filename, difficulty }
      : null

  const importChart = async () => {
    if (!importSource) return
    setImporting(true)
    setError('')
    try {
      const project = await api.importPm3(importSource.root_id, importSource.path, importSource.difficulty)
      onImported(project, importSource.name)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'PM3 导入失败')
    } finally {
      setImporting(false)
    }
  }

  const assignCompare = (side: 'left' | 'right') => {
    if (!currentRef) return
    if (side === 'left') setCompareLeft(currentRef)
    else setCompareRight(currentRef)
    if ((side === 'left' && compareRight) || (side === 'right' && compareLeft)) setTab('compare')
  }

  return (
    <div className="modal-backdrop pm3-backdrop" role="presentation">
      <section className="pm3-research-dialog" role="dialog" aria-modal="true" aria-labelledby="pm3-research-title">
        <header className="pm3-research-header">
          <div className="pm3-title">
            <Microscope size={18} />
            <span><strong id="pm3-research-title">PM3 只读研究工作台</strong><small>A36 / SCRIPT / MEDIA</small></span>
          </div>
          <nav className="pm3-tabs" aria-label="研究视图">
            <button type="button" className={tab === 'catalog' ? 'active' : ''} onClick={() => setTab('catalog')}>
              <ListMusic size={15} />曲目目录
            </button>
            <button type="button" className={tab === 'files' ? 'active' : ''} onClick={() => setTab('files')}>
              <FolderOpen size={15} />文件
            </button>
            <button type="button" className={tab === 'compare' ? 'active' : ''} onClick={() => setTab('compare')}>
              <GitCompareArrows size={15} />比较
            </button>
            <button type="button" className={tab === 'audit' ? 'active' : ''} onClick={() => setTab('audit')}>
              <ShieldCheck size={15} />OTA 审计
            </button>
          </nav>
          <div className="pm3-header-actions">
            {loading && <LoaderCircle className="spin" size={15} />}
            <span className="read-only-badge"><Check size={11} />READ ONLY</span>
            <button type="button" className="icon-button" onClick={onClose} disabled={importing} title="关闭">
              <X size={17} />
            </button>
          </div>
        </header>

        {error && <div className="pm3-error" role="alert"><TriangleAlert size={14} /><span>{error}</span><button type="button" onClick={() => setError('')} title="关闭"><X size={13} /></button></div>}

        <div className="pm3-research-main">
          {tab === 'catalog' && (
            <div className="pm3-catalog-layout">
              <section className="pm3-catalog-pane">
                <div className="pm3-searchbar">
                  <Search size={14} />
                  <input value={search} onChange={(event) => { setSearch(event.target.value); setCatalogOffset(0) }} placeholder="曲名、歌手、ID 或文件名" aria-label="搜索 PM3 曲目" />
                  <span>{catalog?.total ?? 0}</span>
                </div>
                <div className="pm3-table-head catalog"><span>ID</span><span>曲目</span><span>难度</span><span>LV</span><span>来源</span></div>
                <div className="pm3-catalog-list">
                  {catalog?.records.map((record) => (
                    <button
                      type="button"
                      key={`${record.root_id}:${record.path}`}
                      className={selectedRecord?.path === record.path && selectedRecord.root_id === record.root_id ? 'selected' : ''}
                      onClick={() => void chooseRecord(record)}
                    >
                      <span className="mono">{String(record.song_id).padStart(3, '0')}</span>
                      <span className="song-cell"><strong>{record.song_name}</strong><small>{record.singer_name} · {record.filename}</small></span>
                      <span className={`difficulty-pill ${record.difficulty}`}>{DIFFICULTY_LABELS[record.difficulty]}</span>
                      <b>{record.level}</b>
                      <span className={`source-pill ${record.root_id}`}>{record.root_id === 'rewrite' ? 'DL' : 'ROM'}</span>
                    </button>
                  ))}
                  {catalog && !catalog.records.length && <div className="pm3-empty"><Search size={22} /><span>没有匹配记录</span></div>}
                </div>
                <div className="pm3-list-pagination">
                  <button type="button" disabled={!catalog || catalog.offset === 0} onClick={() => setCatalogOffset(Math.max(0, catalogOffset - 100))}><ArrowLeft size={13} /></button>
                  <span>{catalog ? `${catalog.offset + 1}–${Math.min(catalog.offset + catalog.records.length, catalog.total)} / ${catalog.total}` : '—'}</span>
                  <button type="button" disabled={!catalog || catalog.offset + catalog.records.length >= catalog.total} onClick={() => setCatalogOffset(catalogOffset + 100)}><ChevronRight size={13} /></button>
                </div>
              </section>
              <ChartInspector inspection={selectedRecord ? chartInspection : null} record={selectedRecord} loading={loading} />
            </div>
          )}

          {tab === 'files' && (
            <div className="pm3-files-view">
              <div className="pm3-pathbar">
                <select value={rootId} onChange={(event) => void loadDirectory(event.target.value)} aria-label="PM3 根目录">
                  {roots.map((root) => <option key={root.id} value={root.id} disabled={!root.available}>{root.label}{root.available ? '' : '（不可用）'}</option>)}
                </select>
                <div className="pm3-breadcrumbs">
                  {breadcrumbs.map((item, index) => (
                    <span key={`${item.path}:${index}`}>
                      {index > 0 && <ChevronRight size={12} />}
                      <button type="button" onClick={() => void loadDirectory(rootId, item.path)}>{item.label}</button>
                    </span>
                  ))}
                </div>
                <button type="button" className="icon-button" onClick={() => void loadDirectory(rootId, listing?.path)} title="刷新"><RefreshCw size={14} /></button>
              </div>
              <div className="pm3-file-layout">
                <section className="pm3-file-pane">
                  <div className="pm3-table-head files"><span>名称</span><span>类型</span><span>大小</span></div>
                  <div className="pm3-file-list">
                    {listing?.parent != null && (
                      <button type="button" onClick={() => void loadDirectory(rootId, listing.parent ?? '')}>
                        <span className="file-name"><ArrowLeft size={14} /><strong>上一级</strong></span><span>directory</span><span>—</span>
                      </button>
                    )}
                    {visibleFileEntries.map((entry) => (
                      <button
                        type="button"
                        key={entry.path}
                        className={selectedEntry?.path === entry.path ? 'selected' : ''}
                        onClick={() => void openEntry(entry)}
                      >
                        <span className="file-name">{entry.type === 'directory' ? <Folder size={14} /> : <File size={14} />}<strong>{entry.name}</strong></span>
                        <span>{entry.format}</span><span>{formatBytes(entry.size)}</span>
                      </button>
                    ))}
                  </div>
                  <div className="pm3-list-pagination">
                    <button type="button" disabled={filePage === 0} onClick={() => setFilePage((page) => Math.max(0, page - 1))}><ArrowLeft size={13} /></button>
                    <span>{listing ? `${filePage * 200 + 1}–${Math.min((filePage + 1) * 200, listing.entries.length)} / ${listing.entries.length}` : '—'}</span>
                    <button type="button" disabled={!listing || (filePage + 1) * 200 >= listing.entries.length} onClick={() => setFilePage((page) => page + 1)}><ChevronRight size={13} /></button>
                  </div>
                </section>
                <section className="pm3-file-inspector">
                  <div className="pm3-viewer-toolbar">
                    <div className="pm3-view-tabs">
                      <button type="button" className={viewerTab === 'hex' ? 'active' : ''} onClick={() => setViewerTab('hex')} disabled={!fileInspection}><Binary size={14} />HEX</button>
                      <button type="button" className={viewerTab === 'text' ? 'active' : ''} onClick={() => setViewerTab('text')} disabled={!fileInspection?.text}><FileText size={14} />文本</button>
                      <button type="button" className={viewerTab === 'chart' ? 'active' : ''} onClick={() => setViewerTab('chart')} disabled={!selectedFileChart}><Info size={14} />谱面</button>
                    </div>
                    <div>
                      <button type="button" className="icon-button" onClick={() => assignCompare('left')} disabled={!currentRef} title="设为比较左侧"><PanelLeft size={15} /></button>
                      <button type="button" className="icon-button" onClick={() => assignCompare('right')} disabled={!currentRef} title="设为比较右侧"><PanelRight size={15} /></button>
                    </div>
                  </div>
                  {fileInspection && viewerTab === 'hex' && <HexViewer inspection={fileInspection} onPage={(offset) => void inspectPage(offset)} />}
                  {fileInspection && viewerTab === 'text' && <pre className="pm3-text-view">{fileInspection.text}</pre>}
                  {selectedFileChart && viewerTab === 'chart' && <ChartInspector inspection={selectedFileChart} record={null} loading={loading} compact />}
                  {!fileInspection && <div className="pm3-empty"><Binary size={24} /><span>未选择文件</span></div>}
                </section>
              </div>
            </div>
          )}

          {tab === 'compare' && (
            <div className="pm3-compare-view">
              <div className="pm3-compare-sources">
                <div><PanelLeft size={15} /><span><small>LEFT</small><strong>{sourceLabel(compareLeft)}</strong></span>{compareLeft && <button type="button" onClick={() => setCompareLeft(null)} title="清除"><X size={13} /></button>}</div>
                <GitCompareArrows size={18} />
                <div><PanelRight size={15} /><span><small>RIGHT</small><strong>{sourceLabel(compareRight)}</strong></span>{compareRight && <button type="button" onClick={() => setCompareRight(null)} title="清除"><X size={13} /></button>}</div>
              </div>
              {diff ? <DiffViewer diff={diff} /> : <div className="pm3-empty"><GitCompareArrows size={28} /><span>等待左右文件</span></div>}
            </div>
          )}

          {tab === 'audit' && <Pm3OtaAuditView />}
        </div>

        <footer className="pm3-research-footer">
          <span>{tab === 'files' ? `${rootId}:${listing?.path || '/'}` : tab === 'catalog' && selectedRecord && chartInspection ? `${chartInspection.filename} · SLOT ${chartInspection.slot}` : tab === 'audit' ? 'LOCAL OTA SIMULATION · NO HOST WRITE' : 'PM3 RESEARCH'}</span>
          <div>
            <button type="button" className="button secondary" onClick={onClose} disabled={importing}>关闭</button>
            {tab !== 'audit' && <button type="button" className="button primary" onClick={() => void importChart()} disabled={!importSource || importing}>
              {importing ? <LoaderCircle className="spin" size={15} /> : <Import size={15} />}导入谱面
            </button>}
          </div>
        </footer>
      </section>
    </div>
  )
}

function ChartInspector({
  inspection,
  record,
  loading,
  compact = false,
}: {
  inspection: Pm3ChartInspection | null
  record: Pm3CatalogRecord | null
  loading: boolean
  compact?: boolean
}) {
  if (!inspection) return <section className={`pm3-chart-inspector ${compact ? 'compact' : ''}`}><div className="pm3-empty">{loading ? <LoaderCircle className="spin" size={24} /> : <ListMusic size={26} />}<span>{loading ? '正在解密谱面' : '未选择谱面'}</span></div></section>
  const song = record ?? inspection.song ?? null
  return (
    <section className={`pm3-chart-inspector ${compact ? 'compact' : ''}`}>
      <header>
        <span className="chart-format"><Binary size={13} />PM3 / SLOT {inspection.slot}</span>
        <h3>{song?.song_name ?? inspection.filename}</h3>
        <p>{song?.singer_name ?? inspection.path}</p>
      </header>
      <div className="pm3-chart-metrics">
        <span><small>事件</small><strong>{inspection.playable_events}</strong></span>
        <span><small>音符对象</small><strong>{inspection.note_objects}</strong></span>
        <span><small>长音</small><strong>{inspection.hold_notes}</strong></span>
        <span><small>WAV</small><strong>{inspection.wav_count}</strong></span>
      </div>
      <dl className="pm3-chart-details">
        <div><dt>容器</dt><dd>{inspection.used_cut ? 'ENCCUT + A36 CUT' : 'ENC'}</dd></div>
        <div><dt>Header</dt><dd>{inspection.header}</dd></div>
        <div><dt>明文</dt><dd>{formatBytes(inspection.plain_length)} · {inspection.encoding.toUpperCase()}</dd></div>
        <div><dt>BPM</dt><dd>{inspection.bpm_changes.map((event) => event.bpm).join(' / ') || '—'}</dd></div>
        <div><dt>节拍</dt><dd>{inspection.rhythm_changes.map((event) => `${event.beats}/4 @ ${event.tick}`).join(', ') || '—'}</dd></div>
        <div><dt>Track</dt><dd>{inspection.track_ids.join(', ')}</dd></div>
        <div><dt>辅助事件</dt><dd>{inspection.auxiliary_events}</dd></div>
        <div><dt>SHA-256</dt><dd className="hash">{inspection.sha256}</dd></div>
      </dl>
      <div className="pm3-resource-summary">
        <span><small>音乐</small><strong>{inspection.resources?.audio.length ?? 0}</strong></span>
        <span><small>Key 音</small><strong>{inspection.resources?.key_sounds.length ?? 0}</strong></span>
        <span><small>MV 引用池</small><strong>{inspection.resources?.mv.length ?? 0}</strong></span>
      </div>
      {!compact && <pre className="pm3-chart-preview">{inspection.text_preview}</pre>}
      {inspection.warnings.length > 0 && <div className="pm3-inline-warning"><TriangleAlert size={13} />{inspection.warnings[0]}</div>}
    </section>
  )
}

function HexViewer({ inspection, onPage }: { inspection: Pm3FileInspection; onPage: (offset: number) => void }) {
  return (
    <div className="pm3-hex-view">
      <div className="pm3-file-summary">
        <span><strong>{inspection.name}</strong><small>{inspection.format} · {formatBytes(inspection.size)}</small></span>
        <code>{inspection.sha256?.slice(0, 16) ?? 'HASH SKIPPED'}</code>
      </div>
      <div className="pm3-hex-head"><span>OFFSET</span><span>00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F</span><span>ASCII</span></div>
      <div className="pm3-hex-rows">
        {inspection.hex_rows.map((row) => <div key={row.offset}><span>{row.offset_hex}</span><code>{row.hex}</code><code>{row.ascii}</code></div>)}
      </div>
      <div className="pm3-page-controls">
        <button type="button" className="button secondary" disabled={!inspection.has_previous} onClick={() => onPage(Math.max(0, inspection.offset - 4096))}><ArrowLeft size={13} />上一页</button>
        <span>{inspection.offset.toString(16).toUpperCase().padStart(8, '0')}</span>
        <button type="button" className="button secondary" disabled={!inspection.has_next} onClick={() => onPage(inspection.offset + inspection.length)}>下一页<ChevronRight size={13} /></button>
      </div>
    </div>
  )
}

function DiffViewer({ diff }: { diff: Pm3DiffResult }) {
  return (
    <div className="pm3-diff-result">
      <div className="pm3-diff-summary">
        <span className={diff.identical ? 'same' : 'changed'}>{diff.identical ? <Check size={15} /> : <GitCompareArrows size={15} />}{diff.identical ? '内容相同' : `${diff.changed_bytes} 字节不同`}</span>
        <span>比较 {formatBytes(diff.compared_bytes)}</span>
        {diff.truncated && <span className="warning">已截取前 2 MB</span>}
      </div>
      {diff.text_diff ? (
        <pre className="pm3-diff-text">{diff.text_diff.map((line, index) => <span key={`${index}:${line}`} className={line.startsWith('+') ? 'add' : line.startsWith('-') ? 'remove' : line.startsWith('@@') ? 'hunk' : ''}>{line || ' '}{'\n'}</span>)}</pre>
      ) : (
        <div className="pm3-binary-diff">
          <div className="pm3-table-head diff"><span>OFFSET</span><span>LEFT WINDOW</span><span>RIGHT WINDOW</span></div>
          {diff.windows.map((window) => <div key={window.offset}><span>{window.offset.toString(16).toUpperCase().padStart(8, '0')}</span><code>{window.left}</code><code>{window.right}</code></div>)}
          {!diff.windows.length && <div className="pm3-empty"><Check size={22} /><span>比较范围内没有差异</span></div>}
        </div>
      )}
    </div>
  )
}
