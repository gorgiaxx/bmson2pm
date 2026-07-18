import {
  CheckCircle2,
  FileCheck2,
  GitMerge,
  HardDrive,
  ListChecks,
  LoaderCircle,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type {
  Pm3ExportSummary,
  Pm3OtaAudit,
  Pm3OtaChain,
  Pm3OtaMirrorAudit,
} from '../types'

type AuditMode = 'exports' | 'mirror'

function exportName(report: Pm3ExportSummary): string {
  return report.version_name || report.filename || report.export_id
}

function formatBytes(value: number | null): string {
  if (value == null) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

function installOrder(reports: Pm3ExportSummary[], ids: string[]): string[] {
  const selected = reports.filter((report) => ids.includes(report.export_id))
  return selected.sort((left, right) => {
    const leftVersion = Number(exportName(left).match(/^ver(\d{3})$/)?.[1] ?? Number.NaN)
    const rightVersion = Number(exportName(right).match(/^ver(\d{3})$/)?.[1] ?? Number.NaN)
    if (Number.isFinite(leftVersion) && Number.isFinite(rightVersion)) return leftVersion - rightVersion
    return left.created_at.localeCompare(right.created_at)
  }).map((report) => report.export_id)
}

export function Pm3OtaAuditView() {
  const [mode, setMode] = useState<AuditMode>('exports')
  const [reports, setReports] = useState<Pm3ExportSummary[]>([])
  const [focusedId, setFocusedId] = useState('')
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [audit, setAudit] = useState<Pm3OtaAudit | null>(null)
  const [chain, setChain] = useState<Pm3OtaChain | null>(null)
  const [mirror, setMirror] = useState<Pm3OtaMirrorAudit | null>(null)
  const [generation, setGeneration] = useState('')
  const [installedVersion, setInstalledVersion] = useState('')
  const [installedEdition, setInstalledEdition] = useState('')
  const [downloadedVersion, setDownloadedVersion] = useState('')
  const [downloadedEdition, setDownloadedEdition] = useState('')
  const [verifyPayloads, setVerifyPayloads] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const loadAudit = useCallback(async (exportId: string) => {
    setLoading(true)
    setError('')
    setChain(null)
    setFocusedId(exportId)
    try {
      setAudit(await api.pm3ExportAudit(exportId))
    } catch (reason) {
      setAudit(null)
      setError(reason instanceof Error ? reason.message : '离线补丁审计失败')
    } finally {
      setLoading(false)
    }
  }, [])

  const loadReports = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const result = await api.pm3Exports()
      setReports(result)
      const next = result.find((report) => report.kind === 'pm3-version') ?? result[0]
      if (next) {
        setSelectedIds([next.export_id])
        await loadAudit(next.export_id)
      } else {
        setFocusedId('')
        setSelectedIds([])
        setAudit(null)
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '无法读取本地导出记录')
    } finally {
      setLoading(false)
    }
  }, [loadAudit])

  useEffect(() => { void loadReports() }, [loadReports])

  const orderedSelection = useMemo(
    () => installOrder(reports, selectedIds),
    [reports, selectedIds],
  )

  const toggleChain = (exportId: string, checked: boolean) => {
    setSelectedIds((current) => checked
      ? [...new Set([...current, exportId])]
      : current.filter((item) => item !== exportId))
  }

  const simulate = async () => {
    setLoading(true)
    setError('')
    try {
      setChain(await api.pm3AuditChain(orderedSelection))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '版本链模拟失败')
    } finally {
      setLoading(false)
    }
  }

  const scanMirror = async () => {
    const numberValue = (value: string) => value.trim() ? Number(value) : undefined
    setLoading(true)
    setError('')
    try {
      const result = await api.pm3OtaMirror({
        generation: numberValue(generation),
        installedVersion: numberValue(installedVersion),
        installedEdition: numberValue(installedEdition),
        downloadedVersion: numberValue(downloadedVersion),
        downloadedEdition: numberValue(downloadedEdition),
        verifyPayloads,
      })
      setMirror(result)
      setGeneration(String(result.generation))
      setInstalledVersion(String(result.installed.version))
      setInstalledEdition(String(result.installed.edition))
      setDownloadedVersion(String(result.downloaded.version))
      setDownloadedEdition(String(result.downloaded.edition))
    } catch (reason) {
      setMirror(null)
      setError(reason instanceof Error ? reason.message : 'FTP 镜像审计失败')
    } finally {
      setLoading(false)
    }
  }

  const switchMode = (next: AuditMode) => {
    setMode(next)
    setError('')
  }

  return (
    <div className="pm3-ota-audit-view">
      <section className="pm3-ota-export-pane">
        <header className="pm3-ota-export-toolbar">
          <div className="pm3-ota-mode-tabs" role="tablist" aria-label="OTA 审计来源">
            <button type="button" role="tab" aria-selected={mode === 'exports'} className={mode === 'exports' ? 'selected' : ''} onClick={() => switchMode('exports')}><ShieldCheck size={13} />本地导出</button>
            <button type="button" role="tab" aria-selected={mode === 'mirror'} className={mode === 'mirror' ? 'selected' : ''} onClick={() => switchMode('mirror')}><HardDrive size={13} />FTP 镜像</button>
          </div>
          {mode === 'exports' && <div>
              <button type="button" className="icon-button" onClick={() => void loadReports()} disabled={loading} title="刷新本地导出"><RefreshCw size={14} /></button>
              <button type="button" className="button secondary" onClick={() => void simulate()} disabled={loading || selectedIds.length < 2}>
                <GitMerge size={14} />模拟版本链
              </button>
            </div>}
        </header>
        {mode === 'exports' ? <div className="pm3-ota-export-list" role="listbox" aria-label="本地 PM3 补丁">
          {reports.map((report) => {
            const name = exportName(report)
            return (
              <div className={focusedId === report.export_id ? 'selected' : ''} key={report.export_id}>
                <label title="加入版本链">
                  <input
                    type="checkbox"
                    checked={selectedIds.includes(report.export_id)}
                    onChange={(event) => toggleChain(report.export_id, event.target.checked)}
                    aria-label={`版本链选择 ${name}`}
                  />
                </label>
                <button type="button" onClick={() => void loadAudit(report.export_id)} aria-label={`审计 ${name}`}>
                  <span><strong>{name}</strong><small>{report.title || report.kind || 'PM3 EXPORT'}</small></span>
                  <code>{report.created_at.slice(0, 10)}</code>
                </button>
              </div>
            )
          })}
          {!reports.length && !loading && <div className="pm3-empty"><ShieldAlert size={25} /><span>没有本地 PM3 导出记录</span></div>}
        </div> : <div className="pm3-ota-mirror-form">
          <label className="generation"><span>GENERATION</span><input type="number" min="0" max="999" value={generation} placeholder="CFG" onChange={(event) => setGeneration(event.target.value)} aria-label="FTP generation" /></label>
          <fieldset>
            <legend>INSTALLED</legend>
            <label><span>VERSION</span><input type="number" min="0" max="999" value={installedVersion} placeholder="CFG" onChange={(event) => setInstalledVersion(event.target.value)} aria-label="已安装 version" /></label>
            <label><span>EDITION</span><input type="number" min="0" max="999" value={installedEdition} placeholder="CFG" onChange={(event) => setInstalledEdition(event.target.value)} aria-label="已安装 edition" /></label>
          </fieldset>
          <fieldset>
            <legend>TARGET</legend>
            <label><span>VERSION</span><input type="number" min="0" max="999" value={downloadedVersion} placeholder="CFG" onChange={(event) => setDownloadedVersion(event.target.value)} aria-label="目标 version" /></label>
            <label><span>EDITION</span><input type="number" min="0" max="999" value={downloadedEdition} placeholder="CFG" onChange={(event) => setDownloadedEdition(event.target.value)} aria-label="目标 edition" /></label>
          </fieldset>
          <label className="pm3-ota-verify-toggle"><input type="checkbox" checked={verifyPayloads} onChange={(event) => setVerifyPayloads(event.target.checked)} /><span><strong>完整 MD5</strong><small>VERIFY PAYLOADS</small></span></label>
          <button type="button" className="button primary" onClick={() => void scanMirror()} disabled={loading}><Search size={14} />扫描镜像</button>
          {mirror && <div className="pm3-ota-mirror-source"><span>{mirror.patch_root}</span><code>{mirror.root}</code></div>}
        </div>}
        <footer>{mode === 'exports' ? <><span>{selectedIds.length} SELECTED</span><code>INSTALL ORDER: {orderedSelection.length}</code></> : <><span>READ ONLY</span><code>{verifyPayloads ? 'FULL MD5' : 'STRUCTURE'}</code></>}</footer>
      </section>

      <section className="pm3-ota-result-pane">
        {loading && ((mode === 'exports' && !audit) || (mode === 'mirror' && !mirror)) && <div className="pm3-empty"><LoaderCircle className="spin" size={25} /><span>正在读取补丁</span></div>}
        {error && <div className="pm3-ota-result-error"><TriangleAlert size={15} /><span>{error}</span></div>}
        {!error && mode === 'mirror' && mirror ? <MirrorResult mirror={mirror} loading={loading} /> : null}
        {!error && mode === 'mirror' && !mirror && !loading ? <div className="pm3-empty"><HardDrive size={25} /><span>FTP MIRROR · NOT SCANNED</span></div> : null}
        {!error && mode === 'exports' && chain ? <ChainResult chain={chain} /> : !error && mode === 'exports' && audit ? <AuditResult audit={audit} loading={loading} /> : null}
      </section>
    </div>
  )
}

function MirrorResult({ mirror, loading }: { mirror: Pm3OtaMirrorAudit; loading: boolean }) {
  const status = !mirror.valid
    ? 'FAILED'
    : mirror.integrity_verified ? 'VERIFIED' : 'STRUCTURE OK'
  return (
    <div className="pm3-ota-result mirror-result">
      <header className="pm3-ota-result-header">
        <span className={mirror.valid ? 'valid' : 'invalid'}>{mirror.valid ? <HardDrive size={17} /> : <ShieldAlert size={17} />}</span>
        <span><strong>{mirror.patch_root} · GENERATION {mirror.generation}</strong><small>v{mirror.installed.version}e{mirror.installed.edition} → v{mirror.downloaded.version}e{mirror.downloaded.edition}</small></span>
        <code>{loading ? 'CHECKING' : status}</code>
      </header>
      <div className="pm3-ota-stats">
        <span><small>PACKAGES</small><strong>{mirror.counts.packages}</strong></span>
        <span><small>OPERATIONS</small><strong>{mirror.counts.operations}</strong></span>
        <span><small>VERSIONS</small><strong>{mirror.counts.versions}</strong></span>
        <span><small>EDITIONS</small><strong>{mirror.counts.editions}</strong></span>
      </div>
      <div className="pm3-ota-summary-band">
        <span><b>{mirror.plan.steps.length}</b> PLAN</span>
        <span><b>{mirror.counts.invalid_packages}</b> INVALID</span>
        <span><b>{mirror.counts.md5_mismatches}</b> MD5</span>
        <span><b>{mirror.counts.cumulative_breaks}</b> CUMULATIVE</span>
        <code>{mirror.verify_payloads ? `${mirror.counts.verified_payloads} VERIFIED` : 'PRESENCE ONLY'}</code>
      </div>
      <div className="pm3-ota-package-head"><span>TYPE</span><span>补丁目录</span><span>操作</span><span>计划</span><span>完整性</span></div>
      <div className="pm3-ota-package-list">
        {mirror.packages.map((item) => (
          <div key={item.name}>
            <b className={item.kind}>{item.kind === 'version' ? 'V' : 'E'}</b>
            <span><code>{item.name}</code><small>{item.activation_time?.slice(0, 10) || 'TIMESTAMP 0'}{item.cumulative === false ? ' · NON-CUMULATIVE' : ''}</small></span>
            <code>{item.operation_count}</code>
            <em className={item.planned ? 'planned' : ''}>{item.planned ? 'PLANNED' : 'CATALOG'}</em>
            <span className={item.valid ? 'pass' : 'fail'}>{item.valid ? <CheckCircle2 size={13} /> : <ShieldAlert size={13} />}{mirror.verify_payloads ? `${item.counts.verified_payloads}/${item.operation_count - item.counts.delete}` : item.counts.missing_payloads ? 'MISSING' : 'PRESENT'}</span>
          </div>
        ))}
      </div>
      <AuditMessages errors={mirror.errors} warnings={mirror.warnings} />
    </div>
  )
}

function AuditResult({ audit, loading }: { audit: Pm3OtaAudit; loading: boolean }) {
  return (
    <div className="pm3-ota-result">
      <header className="pm3-ota-result-header">
        <span className={audit.valid ? 'valid' : 'invalid'}>{audit.valid ? <ShieldCheck size={17} /> : <ShieldAlert size={17} />}</span>
        <span><strong>{audit.version_name}</strong><small>{audit.export_id}</small></span>
        <code>{loading ? 'CHECKING' : audit.valid ? 'VERIFIED' : 'FAILED'}</code>
      </header>
      <div className="pm3-ota-stats">
        <span><small>OPERATIONS</small><strong>{audit.operation_count}</strong></span>
        <span><small>VERIFIED</small><strong>{audit.counts.verified}</strong></span>
        <span><small>ROM</small><strong>{audit.rom.count}</strong></span>
        <span><small>SONGLIST</small><strong>{audit.song_list?.row_count ?? '—'}</strong></span>
      </div>
      <div className="pm3-ota-summary-band">
        <span><b>{audit.counts.replace}</b> REPLACE</span>
        <span><b>{audit.counts.create}</b> CREATE</span>
        <span><b>{audit.counts.delete}</b> DELETE</span>
        <span><b>{audit.unmanaged_count}</b> UNMANAGED</span>
        <code>{audit.activation_timestamp === 0 ? 'TIMESTAMP 0' : audit.activation_time}</code>
      </div>
      <div className="pm3-ota-operation-head"><span>OP</span><span>路径</span><span>效果</span><span>基线</span><span>校验</span></div>
      <div className="pm3-ota-operation-list">
        {audit.operations.slice(0, 200).map((operation) => (
          <div key={`${operation.line}:${operation.path}`}>
            <b className={operation.action === 'r' ? 'replace' : 'delete'}>{operation.action.toUpperCase()}</b>
            <span><code>{operation.path}</code><small>{operation.format_detail || operation.format} · {formatBytes(operation.size)}</small></span>
            <em className={operation.effect}>{operation.effect.toUpperCase()}</em>
            <code>{operation.baseline.status.toUpperCase()}</code>
            <span className={operation.verified && operation.format_valid !== false ? 'pass' : 'fail'}>
              {operation.verified && operation.format_valid !== false ? <CheckCircle2 size={13} /> : <ShieldAlert size={13} />}
              {operation.actual_md5?.slice(0, 8) || 'DELETE'}
            </span>
          </div>
        ))}
        {audit.operations.length > 200 && <div className="pm3-artifact-more">+ {audit.operations.length - 200} OPERATIONS</div>}
      </div>
      <AuditMessages errors={audit.errors} warnings={audit.warnings} />
    </div>
  )
}

function ChainResult({ chain }: { chain: Pm3OtaChain }) {
  return (
    <div className="pm3-ota-result chain-result">
      <header className="pm3-ota-result-header">
        <span className={chain.valid ? 'valid' : 'invalid'}>{chain.valid ? <GitMerge size={17} /> : <ShieldAlert size={17} />}</span>
        <span><strong>版本链模拟</strong><small>{chain.versions.map((item) => item.version_name).join(' → ')}</small></span>
        <code>{chain.valid ? 'CONSISTENT' : 'CONFLICT'}</code>
      </header>
      <div className="pm3-ota-stats">
        <span><small>VERSIONS</small><strong>{chain.counts.versions}</strong></span>
        <span><small>OPERATIONS</small><strong>{chain.counts.operations}</strong></span>
        <span><small>OVERRIDES</small><strong>{chain.counts.overrides}</strong></span>
        <span><small>FINAL FILES</small><strong>{chain.counts.final_files}</strong></span>
      </div>
      <div className="pm3-ota-operation-head chain"><span>#</span><span>路径</span><span>变更</span><span>版本</span></div>
      <div className="pm3-ota-operation-list chain">
        {chain.transitions.slice(0, 240).map((transition, index) => (
          <div key={`${transition.order}:${transition.path}:${index}`}>
            <b>{transition.order}</b>
            <span><code>{transition.path}</code><small>{transition.after_md5?.slice(0, 12) || 'DELETED'}</small></span>
            <em className={transition.change}>{transition.change.toUpperCase()}</em>
            <code>{transition.version_name}</code>
          </div>
        ))}
      </div>
      {chain.song_list_changes.length > 0 && (
        <section className="pm3-ota-songlist-changes">
          <header><ListChecks size={14} /><strong>SongList 变化</strong></header>
          {chain.song_list_changes.map((change) => (
            <div key={change.export_id}>
              <code>{change.version_name}</code>
              <span className="added">+{change.added.length}</span>
              <span className="removed">-{change.removed.length}</span>
              <small>{[...change.added.slice(0, 3), ...change.removed.slice(0, 3)].join(', ') || 'NO CHANGE'}</small>
            </div>
          ))}
        </section>
      )}
      <AuditMessages errors={chain.errors} warnings={chain.warnings} />
    </div>
  )
}

function AuditMessages({ errors, warnings }: { errors: string[]; warnings: string[] }) {
  if (!errors.length && !warnings.length) return null
  return (
    <section className="pm3-ota-messages">
      {errors.map((message) => <div className="error" key={message}><ShieldAlert size={13} /><span>{message}</span></div>)}
      {warnings.map((message) => <div key={message}><FileCheck2 size={13} /><span>{message}</span></div>)}
    </section>
  )
}
