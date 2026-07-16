import {
  AlertTriangle,
  FileAudio,
  LoaderCircle,
  Play,
  Search,
  Trash2,
  Upload,
} from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { useEditorStore } from '../store'
import type { KeySoundAsset } from '../types'

interface KeySoundLibraryProps {
  onTriggerKeySound: (assetId: string) => void
}

interface AssetUsage {
  lanes: number
  notes: number
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function editorResourcePath(asset: KeySoundAsset): string | null {
  const editor = record(asset.extensions?.editor)
  const resource = record(editor?.resource)
  return typeof resource?.path === 'string' ? resource.path : null
}

export function KeySoundLibrary({ onTriggerKeySound }: KeySoundLibraryProps) {
  const project = useEditorStore((state) => state.project)
  const activeLaneId = useEditorStore((state) => state.activeLaneId)
  const setActiveLane = useEditorStore((state) => state.setActiveLane)
  const addKeySound = useEditorStore((state) => state.addKeySound)
  const updateKeySound = useEditorStore((state) => state.updateKeySound)
  const removeKeySound = useEditorStore((state) => state.removeKeySound)
  const setLaneDefaultKeySound = useEditorStore((state) => state.setLaneDefaultKeySound)
  const uploadRef = useRef<HTMLInputElement>(null)
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const usageByAsset = useMemo(() => {
    const usage = new Map<string, AssetUsage>(
      project.key_sounds.map((asset) => [asset.id, { lanes: 0, notes: 0 }]),
    )
    for (const lane of project.lanes) {
      const current = lane.default_key_sound_id ? usage.get(lane.default_key_sound_id) : null
      if (current) current.lanes += 1
    }
    for (const chart of Object.values(project.difficulties)) {
      for (const note of chart.notes) {
        const current = note.key_sound_id ? usage.get(note.key_sound_id) : null
        if (current) current.notes += 1
      }
    }
    return usage
  }, [project.difficulties, project.key_sounds, project.lanes])

  const normalizedQuery = query.trim().toLocaleLowerCase()
  const filteredAssets = project.key_sounds.filter((asset) => (
    !normalizedQuery
    || `${asset.name} ${asset.filename} ${asset.tags.join(' ')}`.toLowerCase().includes(normalizedQuery)
  ))
  const selectedAsset = filteredAssets.find((asset) => asset.id === selectedId)
    ?? filteredAssets[0]
    ?? null
  const activeLane = project.lanes.find((lane) => lane.id === activeLaneId) ?? project.lanes[0] ?? null
  const selectedUsage = selectedAsset ? usageByAsset.get(selectedAsset.id) ?? { lanes: 0, notes: 0 } : null
  const selectedReferences = selectedUsage ? selectedUsage.lanes + selectedUsage.notes : 0
  const selectedResourcePath = selectedAsset ? editorResourcePath(selectedAsset) : null
  const canDelete = Boolean(
    selectedAsset?.source === 'manual'
    && selectedResourcePath
    && selectedReferences === 0,
  )

  const uploadFiles = async (files: FileList | null) => {
    if (!files?.length) return
    setBusy(true)
    setError(null)
    try {
      let latestId: string | null = null
      for (const file of Array.from(files).slice(0, 64)) {
        const asset = await api.uploadKeySound(project.id, file)
        addKeySound(asset)
        latestId = asset.id
      }
      if (latestId) setSelectedId(latestId)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Key 音上传失败')
    } finally {
      setBusy(false)
      if (uploadRef.current) uploadRef.current.value = ''
    }
  }

  const deleteSelected = async () => {
    if (!selectedAsset || !selectedResourcePath || !canDelete) return
    if (!window.confirm(`删除 Key 音“${selectedAsset.name}”？`)) return
    setBusy(true)
    setError(null)
    if (!removeKeySound(selectedAsset.id)) {
      setBusy(false)
      setError('Key 音已被 Track 或音符引用，无法删除')
      return
    }
    try {
      await api.deleteKeySound(project.id, selectedAsset.id, selectedResourcePath)
      setSelectedId(null)
    } catch (reason) {
      addKeySound(selectedAsset)
      setSelectedId(selectedAsset.id)
      setError(reason instanceof Error ? reason.message : 'Key 音删除失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="inspector-body key-sound-library">
      <div className="inspector-heading key-sound-heading">
        <div><span>资源库</span><strong>{project.key_sounds.length} 个 Key 音</strong></div>
        <button type="button" className="icon-button" onClick={() => uploadRef.current?.click()} disabled={busy} title="上传 Key 音">
          {busy ? <LoaderCircle className="spin" size={15} /> : <Upload size={15} />}
        </button>
        <input
          ref={uploadRef}
          className="visually-hidden"
          type="file"
          accept="audio/*,.wav,.ogg,.mp3,.flac,.aif,.aiff"
          multiple
          aria-label="上传 Key 音文件"
          onChange={(event) => void uploadFiles(event.target.files)}
        />
      </div>

      <fieldset className="property-group key-sound-lane-default">
        <legend>Track 默认音色</legend>
        <label><span>Track</span>
          <select value={activeLane?.id ?? ''} onChange={(event) => setActiveLane(Number(event.target.value))}>
            {project.lanes.map((lane) => <option key={lane.id} value={lane.id}>{lane.display_name}</option>)}
          </select>
        </label>
        <label><span>默认音色</span>
          <select
            value={activeLane?.default_key_sound_id ?? ''}
            onChange={(event) => {
              if (activeLane) setLaneDefaultKeySound(activeLane.id, event.target.value || null)
            }}
            disabled={!activeLane}
          >
            <option value="">合成兜底音</option>
            {project.key_sounds.map((asset) => <option key={asset.id} value={asset.id}>{asset.name}</option>)}
          </select>
        </label>
      </fieldset>

      <div className="key-sound-search">
        <Search size={13} />
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索名称、文件或标签" aria-label="搜索 Key 音" />
      </div>

      <div className="key-sound-list" aria-label="Key 音资源">
        {!filteredAssets.length && (
          <div className="empty-state key-sound-empty">
            <FileAudio size={27} />
            <strong>{project.key_sounds.length ? '没有匹配资源' : '尚无 Key 音'}</strong>
            {!project.key_sounds.length && <button type="button" className="button secondary" onClick={() => uploadRef.current?.click()}>上传音频</button>}
          </div>
        )}
        {filteredAssets.map((asset) => {
          const usage = usageByAsset.get(asset.id) ?? { lanes: 0, notes: 0 }
          return (
            <button
              type="button"
              key={asset.id}
              className={`key-sound-row ${selectedAsset?.id === asset.id ? 'active' : ''}`}
              onClick={() => setSelectedId(asset.id)}
            >
              <FileAudio size={14} />
              <span><strong>{asset.name}</strong><small>{asset.filename}</small></span>
              <b>{usage.lanes + usage.notes ? `${usage.lanes + usage.notes} REF` : asset.source?.toUpperCase() ?? 'ASSET'}</b>
            </button>
          )
        })}
      </div>

      {selectedAsset && selectedUsage && (
        <fieldset className="property-group key-sound-properties">
          <legend>资源属性</legend>
          <label><span>名称</span><input value={selectedAsset.name} onChange={(event) => updateKeySound(selectedAsset.id, { name: event.target.value })} /></label>
          <label><span>音量</span><div className="range-with-value">
            <input type="range" min="0" max="2" step="0.05" value={selectedAsset.volume} onChange={(event) => updateKeySound(selectedAsset.id, { volume: Number(event.target.value) })} />
            <b>{Math.round(selectedAsset.volume * 100)}%</b>
          </div></label>
          <label><span>延迟</span><div className="unit-input"><input type="number" step="1" value={selectedAsset.delay_ms} onChange={(event) => updateKeySound(selectedAsset.id, { delay_ms: Number(event.target.value) })} /><i>ms</i></div></label>
          <label><span>标签</span><input value={selectedAsset.tags.join(', ')} onChange={(event) => updateKeySound(selectedAsset.id, { tags: event.target.value.split(',').map((tag) => tag.trim()).filter(Boolean) })} /></label>
          <div className="key-sound-usage"><span>Track {selectedUsage.lanes}</span><span>Note {selectedUsage.notes}</span><code>{selectedAsset.source ?? 'manual'}</code></div>
          <div className="key-sound-actions">
            <button type="button" className="button secondary" onClick={() => onTriggerKeySound(selectedAsset.id)}><Play size={13} />试听</button>
            <button
              type="button"
              className="icon-button danger"
              onClick={() => void deleteSelected()}
              disabled={!canDelete || busy}
              title={selectedReferences ? '资源仍被 Track 或音符引用' : selectedAsset.source !== 'manual' ? '导入资源由源谱面管理' : '删除 Key 音'}
              aria-label={`删除 ${selectedAsset.name}`}
            ><Trash2 size={14} /></button>
          </div>
        </fieldset>
      )}

      {error && <div className="key-sound-error" role="alert"><AlertTriangle size={13} /><span>{error}</span></div>}
    </div>
  )
}
