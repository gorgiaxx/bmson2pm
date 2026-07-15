import { AlertCircle, CheckCircle2, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, bmsResourcePath } from './api'
import { BmsImportDialog } from './components/BmsImportDialog'
import { EditorToolbar } from './components/EditorToolbar'
import { Header } from './components/Header'
import { Inspector } from './components/Inspector'
import { Pm3ResearchDialog } from './components/Pm3ResearchDialog'
import { Pm3ExportDialog } from './components/Pm3ExportDialog'
import { Sidebar } from './components/Sidebar'
import { Timeline } from './components/Timeline'
import { Transport } from './components/Transport'
import { createDemoProject } from './demo'
import { useAudioFile } from './hooks/useAudioFile'
import { useAutosave } from './hooks/useAutosave'
import { usePlayback } from './hooks/usePlayback'
import { useEditorStore } from './store'
import { createTimingIndex, secondsToPulse } from './timing'
import { validateProjectLocally } from './validation'
import type { BmsImportOptions, BmsInspection, DifficultyId, ExportFormat, ImportFormat, SongProject, ValidationIssue } from './types'

interface Toast { type: 'success' | 'error'; message: string }

interface BmsImportState {
  file: File
  chartFiles: File[]
  resourceFiles: File[]
  inspection: BmsInspection
}

const BMS_CHART_EXTENSIONS = ['.bms', '.bme', '.bml', '.pms']

function hasExtension(file: File, extensions: string[]): boolean {
  const name = file.name.toLowerCase()
  return extensions.some((extension) => name.endsWith(extension))
}

function preferredBmsFile(files: File[], difficulty: DifficultyId): File {
  const marker = difficulty === 'hard' ? /(?:^|[_-])h\./i
    : difficulty === 'special' || difficulty === 'master' ? /(?:^|[_-])a\./i
      : /(?:^|[_-])n\./i
  return files.find((file) => marker.test(file.name)) ?? files[0]
}

function pm3SourceDifficulty(project: SongProject): DifficultyId | null {
  const value = (project.game_specific_data.pm3_song_info as { difficulty?: DifficultyId } | undefined)?.difficulty
  return value && value in project.difficulties ? value : null
}

export default function App() {
  const project = useEditorStore((state) => state.project)
  const difficulty = useEditorStore((state) => state.activeDifficulty)
  const setProject = useEditorStore((state) => state.setProject)
  const setActiveDifficulty = useEditorStore((state) => state.setActiveDifficulty)
  const setTool = useEditorStore((state) => state.setTool)
  const remove = useEditorStore((state) => state.deleteSelected)
  const undo = useEditorStore((state) => state.undo)
  const redo = useEditorStore((state) => state.redo)
  const copy = useEditorStore((state) => state.copySelected)
  const paste = useEditorStore((state) => state.pasteAt)
  const selectAllNotes = useEditorStore((state) => state.selectAllNotes)
  const selectOnly = useEditorStore((state) => state.selectOnly)
  const activeLaneId = useEditorStore((state) => state.activeLaneId)
  const setActiveLane = useEditorStore((state) => state.setActiveLane)
  const createAnonymousLane = useEditorStore((state) => state.createAnonymousLane)
  const updateMetadata = useEditorStore((state) => state.updateMetadata)
  const [connected, setConnected] = useState(false)
  const [loading, setLoading] = useState(true)
  const [issues, setIssues] = useState<ValidationIssue[]>([])
  const [validating, setValidating] = useState(false)
  const [toast, setToast] = useState<Toast | null>(null)
  const [bmsImport, setBmsImport] = useState<BmsImportState | null>(null)
  const [bmsImportBusy, setBmsImportBusy] = useState(false)
  const [pm3ResearchOpen, setPm3ResearchOpen] = useState(false)
  const [pm3ExportOpen, setPm3ExportOpen] = useState(false)
  const { audio, error: audioError, load: loadAudio, clear: clearAudio } = useAudioFile()
  const playback = usePlayback(project, difficulty, audio?.buffer ?? null)
  const timingIndex = useMemo(() => createTimingIndex(project.timing), [project.timing])
  useAutosave(connected && !loading)

  const showToast = useCallback((type: Toast['type'], message: string) => {
    setToast({ type, message })
    window.setTimeout(() => setToast(null), 3600)
  }, [])

  useEffect(() => {
    let cancelled = false
    const loadInitial = async () => {
      try {
        const summaries = await api.listProjects()
        if (cancelled) return
        setConnected(true)
        if (summaries.length) {
          const saved = await api.getProject(summaries[0].id)
          setProject(saved)
          const sourceDifficulty = pm3SourceDifficulty(saved)
          if (sourceDifficulty) setActiveDifficulty(sourceDifficulty)
        } else {
          const base = await api.createProject('Neon Pulse', 'BMSON2PM Demo', 128)
          const demo = createDemoProject(base.id)
          demo.created_at = base.created_at
          await api.saveProject(demo)
          setProject(demo)
        }
      } catch {
        if (cancelled) return
        setConnected(false)
        try {
          const cached = JSON.parse(localStorage.getItem('bmson2pm:last-project') ?? 'null') as SongProject | null
          if (cached?.schema_version && cached?.difficulties) setProject(cached)
        } catch { /* use built-in demo */ }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void loadInitial()
    return () => { cancelled = true }
  }, [setActiveDifficulty, setProject])

  const validate = useCallback(async () => {
    setValidating(true)
    try {
      const result = connected ? await api.validateProject(project.id, difficulty) : validateProjectLocally(project, difficulty)
      setIssues(result)
    } catch (reason) {
      setIssues(validateProjectLocally(project, difficulty))
      showToast('error', reason instanceof Error ? reason.message : '验证失败')
    } finally {
      setValidating(false)
    }
  }, [connected, difficulty, project, showToast])

  useEffect(() => {
    const timer = window.setTimeout(() => setIssues(validateProjectLocally(project, difficulty)), 250)
    return () => window.clearTimeout(timer)
  }, [difficulty, project])

  const handleImport = useCallback(async (files: File[], format: ImportFormat) => {
    if (!connected) {
      showToast('error', '谱面导入需要启动 FastAPI 服务')
      return
    }
    const file = files[0]
    if (!file) return
    try {
      if (format === 'bms') {
        const chartFiles = files
          .filter((item) => hasExtension(item, BMS_CHART_EXTENSIONS))
          .sort((left, right) => bmsResourcePath(left).localeCompare(bmsResourcePath(right)))
        if (!chartFiles.length) throw new Error('所选目录中没有 BMS、BME、BML 或 PMS 谱面')
        const selectedFile = preferredBmsFile(chartFiles, difficulty)
        const resourceFiles = files.filter((item) => !hasExtension(item, BMS_CHART_EXTENSIONS))
        setBmsImportBusy(true)
        const inspection = await api.inspectBms(selectedFile)
        setBmsImport({ file: selectedFile, chartFiles, resourceFiles, inspection })
        return
      }
      playback.stop()
      const imported = format === 'notelist'
        ? await api.importNoteList(file, difficulty)
        : await api.importBmson(file, difficulty)
      clearAudio()
      setProject(imported)
      const label = format === 'notelist' ? 'NoteList JSON' : 'BMSON'
      showToast('success', `已导入 ${file.name} · ${label}`)
    } catch (reason) {
      showToast('error', reason instanceof Error ? reason.message : '导入失败')
    } finally {
      setBmsImportBusy(false)
    }
  }, [clearAudio, connected, difficulty, playback, setProject, showToast])

  const changeBmsEncoding = useCallback(async (encoding: string) => {
    if (!bmsImport) return
    setBmsImportBusy(true)
    try {
      const inspection = await api.inspectBms(bmsImport.file, encoding)
      setBmsImport({ ...bmsImport, inspection })
    } catch (reason) {
      showToast('error', reason instanceof Error ? reason.message : '编码预检失败')
    } finally {
      setBmsImportBusy(false)
    }
  }, [bmsImport, showToast])

  const changeBmsChart = useCallback(async (path: string) => {
    if (!bmsImport) return
    const file = bmsImport.chartFiles.find((item) => bmsResourcePath(item) === path)
    if (!file || file === bmsImport.file) return
    setBmsImportBusy(true)
    try {
      const inspection = await api.inspectBms(file)
      setBmsImport({ ...bmsImport, file, inspection })
    } catch (reason) {
      showToast('error', reason instanceof Error ? reason.message : 'BMS 谱面预检失败')
    } finally {
      setBmsImportBusy(false)
    }
  }, [bmsImport, showToast])

  const changeBmsResources = useCallback((files: File[]) => {
    if (!bmsImport) return
    const resourceFiles = files.filter((item) => !hasExtension(item, BMS_CHART_EXTENSIONS))
    const discoveredCharts = files.filter((item) => hasExtension(item, BMS_CHART_EXTENSIONS))
    const known = new Map(bmsImport.chartFiles.map((item) => [bmsResourcePath(item), item]))
    for (const file of discoveredCharts) known.set(bmsResourcePath(file), file)
    setBmsImport({
      ...bmsImport,
      chartFiles: [...known.values()].sort((left, right) => bmsResourcePath(left).localeCompare(bmsResourcePath(right))),
      resourceFiles,
    })
  }, [bmsImport])

  const confirmBmsImport = useCallback(async (options: BmsImportOptions) => {
    if (!bmsImport) return
    setBmsImportBusy(true)
    try {
      playback.stop()
      const imported = await api.importBms(bmsImport.file, difficulty, options, bmsImport.resourceFiles)
      clearAudio()
      setProject(imported)
      const warnings = Array.isArray(imported.unknown_data.import_warnings)
        ? imported.unknown_data.import_warnings.length
        : 0
      setBmsImport(null)
      const resourceReport = imported.unknown_data.bms_resource_report as { matched?: number } | undefined
      const visualReport = imported.unknown_data.bms_visual_resource_report as { matched?: number } | undefined
      const matched = typeof resourceReport?.matched === 'number' ? ` · ${resourceReport.matched} 个 Key 音` : ''
      const visualMatched = typeof visualReport?.matched === 'number' ? ` · ${visualReport.matched} 个 BGA` : ''
      showToast('success', `已导入 ${bmsImport.file.name}${matched}${visualMatched}${warnings ? ` · ${warnings} 条提示` : ''}`)
    } catch (reason) {
      showToast('error', reason instanceof Error ? reason.message : 'BMS 导入失败')
    } finally {
      setBmsImportBusy(false)
    }
  }, [bmsImport, clearAudio, difficulty, playback, setProject, showToast])

  const handleExport = useCallback(async (format: ExportFormat) => {
    if (!connected) {
      showToast('error', '谱面导出需要启动 FastAPI 服务')
      return
    }
    try {
      await api.saveProject(project)
      const baseIssues = await api.validateProject(project.id, difficulty)
      const compatibility = format === 'bms' ? await api.bmsCompatibility(project.id, difficulty) : []
      const found = [...baseIssues, ...compatibility]
      setIssues(found)
      if (found.some((issue) => issue.severity === 'error')) {
        showToast('error', '请先解决阻止导出的错误')
        return
      }
      if (format === 'pm3') {
        setPm3ExportOpen(true)
        return
      }
      if (format === 'bms') await api.exportBms(project, difficulty)
      else if (format === 'notelist') await api.exportNoteList(project, difficulty)
      else await api.exportBmson(project, difficulty)
      const warningCount = compatibility.filter((issue) => issue.severity === 'warning').length
      const label = format === 'notelist' ? 'NoteList JSON' : format.toUpperCase()
      showToast('success', `已导出 ${project.difficulties[difficulty].display_name} ${label}${warningCount ? ` · ${warningCount} 条兼容性提示` : ''}`)
    } catch (reason) {
      showToast('error', reason instanceof Error ? reason.message : '导出失败')
    }
  }, [connected, difficulty, project, showToast])

  const handleNewProject = useCallback(async () => {
    playback.stop()
    clearAudio()
    try {
      if (connected) {
        const fresh = await api.createProject('未命名曲目', '未知艺术家', 120)
        setProject(fresh)
      } else {
        const fresh = createDemoProject()
        fresh.metadata.title = '未命名曲目'
        fresh.metadata.artist = '未知艺术家'
        Object.values(fresh.difficulties).forEach((chart) => { chart.notes = [] })
        setProject(fresh)
      }
      showToast('success', '已创建新项目')
    } catch (reason) {
      showToast('error', reason instanceof Error ? reason.message : '新建失败')
    }
  }, [clearAudio, connected, playback, setProject, showToast])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement
      if (target.matches('input, textarea, select, [contenteditable="true"]')) return
      const modifier = event.metaKey || event.ctrlKey
      if (event.code === 'Space') {
        event.preventDefault()
        playback.playing ? playback.pause() : void playback.play()
      } else if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault(); remove()
      } else if (modifier && event.key.toLowerCase() === 'z') {
        event.preventDefault(); event.shiftKey ? redo() : undo()
      } else if (modifier && event.key.toLowerCase() === 'y') {
        event.preventDefault(); redo()
      } else if (modifier && event.key.toLowerCase() === 'a') {
        event.preventDefault(); event.shiftKey ? selectOnly(null) : selectAllNotes()
      } else if (modifier && event.shiftKey && event.key.toLowerCase() === 'n') {
        event.preventDefault(); createAnonymousLane()
      } else if (modifier && event.key.toLowerCase() === 'c') {
        event.preventDefault(); copy()
      } else if (modifier && event.key.toLowerCase() === 'v') {
        event.preventDefault(); paste(secondsToPulse(project, playback.position, timingIndex))
      } else if (!modifier && event.key.toLowerCase() === 'v') setTool('select')
      else if (!modifier && event.key.toLowerCase() === 'p') setTool('draw')
      else if (!modifier && (event.key === '[' || event.key === ']')) {
        event.preventDefault()
        const index = Math.max(0, project.lanes.findIndex((lane) => lane.id === activeLaneId))
        const delta = event.key === '[' ? -1 : 1
        const lane = project.lanes[Math.min(project.lanes.length - 1, Math.max(0, index + delta))]
        if (lane) setActiveLane(lane.id)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [activeLaneId, copy, createAnonymousLane, paste, playback, project, redo, remove, selectAllNotes, selectOnly, setActiveLane, setTool, timingIndex, undo])

  const waveformPeaks = audio?.peaks ?? playback.autoMusic?.peaks ?? null
  const waveformStart = audio ? project.timing.audio_offset_ms / 1000 : playback.musicStart
  const waveformDuration = audio?.buffer.duration ?? playback.autoMusic?.buffer.duration ?? 0
  const hasMusic = Boolean(audio || playback.autoMusic || playback.autoMusicLoading || playback.bgmEventCount)
  const audioName = audio?.name
    ?? (playback.autoMusic ? `自动 · ${playback.autoMusic.name}` : null)
    ?? (playback.autoMusicLoading ? `正在载入 · ${playback.autoMusicLoading}` : null)
    ?? (playback.bgmEventCount ? `BMS 自动音频 · ${playback.bgmEventCount} 事件` : null)
  const duration = playback.duration
  const currentPulse = useMemo(
    () => secondsToPulse(project, playback.position, timingIndex),
    [playback.position, project, timingIndex],
  )

  return (
    <div className="app-shell">
      <Header connected={connected} onImport={(files, format) => void handleImport(files, format)} onExport={(format) => void handleExport(format)} onNewProject={() => void handleNewProject()} onResearch={() => setPm3ResearchOpen(true)} />
      <div className="workspace">
        <Sidebar />
        <main className="editor-area">
          <EditorToolbar currentPulse={currentPulse} />
          <Timeline
            peaks={waveformPeaks}
            waveformStart={waveformStart}
            waveformDuration={waveformDuration}
            position={playback.position}
            playing={playback.playing}
            keySoundStatus={playback.keySoundStatus}
            hasMusic={hasMusic}
            musicMuted={playback.musicMuted}
            issues={issues}
            onSeek={playback.seek}
            onScrubStart={playback.beginScrub}
            onScrub={playback.scrubTo}
            onScrubEnd={playback.endScrub}
            onMusicMute={playback.setMusicMuted}
            onTriggerLane={playback.triggerLane}
            onTriggerNote={playback.triggerNote}
          />
        </main>
        <Inspector
          issues={issues}
          validating={validating}
          onValidate={() => void validate()}
          position={playback.position}
          playing={playback.playing}
          speed={playback.speed}
        />
      </div>
      <Transport
        playing={playback.playing} position={playback.position} duration={duration}
        speed={playback.speed} loop={playback.loop} musicMuted={playback.musicMuted}
        audioName={audioName} onPlay={() => void playback.play()} onPause={playback.pause}
        onStop={playback.stop} onSeek={playback.seek} onSpeed={playback.setSpeed}
        onLoop={playback.setLoop} onMute={playback.setMusicMuted}
        onAudioFile={(file) => {
          playback.stop()
          void loadAudio(file)
            .then((buffer) => updateMetadata({ audio_duration: buffer.duration }))
            .catch(() => showToast('error', audioError || '音频解码失败'))
        }}
      />
      {bmsImport && (
        <BmsImportDialog
          inspection={bmsImport.inspection}
          chartFiles={bmsImport.chartFiles}
          selectedChartPath={bmsResourcePath(bmsImport.file)}
          resourceFiles={bmsImport.resourceFiles}
          busy={bmsImportBusy}
          onClose={() => setBmsImport(null)}
          onEncodingChange={(encoding) => void changeBmsEncoding(encoding)}
          onChartChange={(path) => void changeBmsChart(path)}
          onResourceFiles={changeBmsResources}
          onConfirm={(options) => void confirmBmsImport(options)}
        />
      )}
      {pm3ResearchOpen && (
        <Pm3ResearchDialog
          difficulty={difficulty}
          onClose={() => setPm3ResearchOpen(false)}
          onImported={(imported, sourceName) => {
            playback.stop()
            clearAudio()
            setProject(imported)
            const importedDifficulty = pm3SourceDifficulty(imported)
            if (importedDifficulty) setActiveDifficulty(importedDifficulty)
            setPm3ResearchOpen(false)
            const warnings = Array.isArray(imported.unknown_data.import_warnings)
              ? imported.unknown_data.import_warnings.length
              : 0
            showToast('success', `已导入 ${sourceName}${warnings ? ` · ${warnings} 条提示` : ''}`)
          }}
        />
      )}
      {pm3ExportOpen && (
        <Pm3ExportDialog
          project={project}
          difficulty={difficulty}
          onClose={() => setPm3ExportOpen(false)}
          onComplete={(report) => showToast(
            'success',
            report.status === 'rolled_back'
              ? `PM3 ${report.filename} 已回滚`
              : `PM3 ${report.filename} ${report.status === 'published' ? '已发布' : '安全包已生成'}`,
          )}
        />
      )}
      {loading && <div className="loading-screen"><span className="brand-mark large"><i /><i /><i /></span><strong>正在打开工作区</strong></div>}
      {toast && (
        <div className={`toast ${toast.type}`} role="status">
          {toast.type === 'success' ? <CheckCircle2 size={17} /> : <AlertCircle size={17} />}
          <span>{toast.message}</span>
          <button type="button" onClick={() => setToast(null)} title="关闭"><X size={14} /></button>
        </div>
      )}
    </div>
  )
}
