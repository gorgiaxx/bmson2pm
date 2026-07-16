import {
  Check,
  ChevronDown,
  CircleAlert,
  Download,
  FileJson2,
  FilePlus2,
  FileText,
  FolderOpen,
  Import,
  ListMusic,
  Layers3,
  LoaderCircle,
  Microscope,
  PackageCheck,
  Save,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useEditorStore } from '../store'
import type { ExportFormat, ImportFormat } from '../types'

const IMPORT_ACCEPT: Record<ImportFormat, string> = {
  bmson: '.bmson,.json,application/json',
  notelist: '.json,application/json',
  bms: '.bms,.bme,.bml,.pms,text/plain',
}

interface HeaderProps {
  connected: boolean
  onImport: (files: File[], format: ImportFormat) => void
  onExport: (format: ExportFormat) => void
  onNewProject: () => void
  onResearch: () => void
}

export function Header({ connected, onImport, onExport, onNewProject, onResearch }: HeaderProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const directoryInputRef = useRef<HTMLInputElement>(null)
  const importRef = useRef<HTMLDivElement>(null)
  const exportRef = useRef<HTMLDivElement>(null)
  const importFormatRef = useRef<ImportFormat>('bmson')
  const [importFormat, setImportFormat] = useState<ImportFormat>('bmson')
  const [importOpen, setImportOpen] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)
  const project = useEditorStore((state) => state.project)
  const saveStatus = useEditorStore((state) => state.saveStatus)
  const saveMessage = useEditorStore((state) => state.saveMessage)
  const updateMetadata = useEditorStore((state) => state.updateMetadata)

  const status = {
    saved: { icon: <Check size={13} />, label: saveMessage ? `已保存 ${saveMessage}` : '全部更改已保存' },
    dirty: { icon: <Save size={13} />, label: '等待自动保存' },
    saving: { icon: <LoaderCircle className="spin" size={13} />, label: '正在保存' },
    error: { icon: <CircleAlert size={13} />, label: saveMessage || '保存失败' },
  }[saveStatus]

  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (!importRef.current?.contains(event.target as Node)) setImportOpen(false)
      if (!exportRef.current?.contains(event.target as Node)) setExportOpen(false)
    }
    window.addEventListener('pointerdown', close)
    return () => window.removeEventListener('pointerdown', close)
  }, [])

  const chooseExport = (format: ExportFormat) => {
    setExportOpen(false)
    onExport(format)
  }

  const chooseImport = (format: ImportFormat) => {
    importFormatRef.current = format
    setImportFormat(format)
    setImportOpen(false)
    window.setTimeout(() => inputRef.current?.click(), 0)
  }

  const chooseBmsDirectory = () => {
    setImportOpen(false)
    window.setTimeout(() => directoryInputRef.current?.click(), 0)
  }

  return (
    <header className="app-header">
      <div className="brand" aria-label="BMSON2PM">
        <span className="brand-mark"><i /><i /><i /></span>
        <span>BMSON<span>2</span>PM</span>
      </div>
      <div className="project-heading">
        <input
          value={project.metadata.title}
          onChange={(event) => updateMetadata({ title: event.target.value })}
          aria-label="曲目名称"
        />
        <div className={`save-state ${saveStatus}`}>{status.icon}<span>{status.label}</span></div>
      </div>
      <div className="header-actions">
        <span className={`connection ${connected ? 'online' : ''}`} title={connected ? 'FastAPI 已连接' : '离线模式'}>
          <i />{connected ? 'API' : 'LOCAL'}
        </span>
        <button type="button" className="icon-button" onClick={onNewProject} title="新建项目">
          <FilePlus2 size={17} />
        </button>
        <button type="button" className="icon-button" onClick={onResearch} disabled={!connected} title="PM3 只读研究工作台">
          <Microscope size={17} />
        </button>
        <input
          ref={inputRef}
          type="file"
          accept={IMPORT_ACCEPT[importFormat]}
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) onImport([file], importFormatRef.current)
            event.currentTarget.value = ''
          }}
        />
        <input
          ref={(node) => {
            directoryInputRef.current = node
            node?.setAttribute('webkitdirectory', '')
          }}
          type="file"
          multiple
          hidden
          onChange={(event) => {
            const files = Array.from(event.target.files ?? [])
            if (files.length) onImport(files, 'bms')
            event.currentTarget.value = ''
          }}
        />
        <div className="export-control" ref={importRef}>
          <button
            type="button"
            className="button secondary format-button"
            onClick={() => { setImportOpen((open) => !open); setExportOpen(false) }}
            aria-label="导入谱面"
            aria-haspopup="menu"
            aria-expanded={importOpen}
          >
            <Import size={15} /><span>导入</span><ChevronDown size={13} />
          </button>
          {importOpen && (
            <div className="export-menu import-menu" role="menu">
              <button type="button" role="menuitem" onClick={() => chooseImport('bmson')}>
                <FileJson2 size={15} /><span><strong>BMSON</strong><small>标准 BMSON JSON</small></span>
              </button>
              <button type="button" role="menuitem" onClick={() => chooseImport('notelist')}>
                <ListMusic size={15} /><span><strong>NoteList JSON</strong><small>TPB 与 samplelist/notelist</small></span>
              </button>
              <button type="button" role="menuitem" onClick={chooseBmsDirectory}>
                <FolderOpen size={15} /><span><strong>传统 BMS 目录</strong><small>谱面与 WAV / OGG 资源</small></span>
              </button>
              <button type="button" role="menuitem" onClick={() => chooseImport('bms')}>
                <FileText size={15} /><span><strong>单独 BMS 谱面</strong><small>稍后在映射界面补充资源</small></span>
              </button>
            </div>
          )}
        </div>
        <div className="export-control" ref={exportRef}>
          <button
            type="button"
            className="button primary export-button"
            onClick={() => { setExportOpen((open) => !open); setImportOpen(false) }}
            aria-label="导出谱面"
            aria-haspopup="menu"
            aria-expanded={exportOpen}
          >
            <Download size={15} /><span>导出</span><ChevronDown size={13} />
          </button>
          {exportOpen && (
            <div className="export-menu" role="menu">
              <button type="button" role="menuitem" onClick={() => chooseExport('bmson')}>
                <FileJson2 size={15} /><span><strong>BMSON</strong><small>通用交换格式</small></span>
              </button>
              <button type="button" role="menuitem" onClick={() => chooseExport('notelist')}>
                <ListMusic size={15} /><span><strong>NoteList JSON</strong><small>音符中心与采样索引</small></span>
              </button>
              <button type="button" role="menuitem" onClick={() => chooseExport('bms')}>
                <FileText size={15} /><span><strong>传统 BMS</strong><small>六路输入通道映射</small></span>
              </button>
              <button type="button" role="menuitem" onClick={() => chooseExport('pm3')} disabled={!connected}>
                <PackageCheck size={15} /><span><strong>PM3 更新包</strong><small>加密谱面与发布报告</small></span>
              </button>
              <button type="button" role="menuitem" onClick={() => chooseExport('pm3-version')} disabled={!connected}>
                <Layers3 size={15} /><span><strong>PM3 离线版本</strong><small>多曲共享 SquashFS ROM</small></span>
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  )
}
