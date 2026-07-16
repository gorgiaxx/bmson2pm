export type DifficultyId = 'easy' | 'normal' | 'hard' | 'special' | 'master'
export type Severity = 'error' | 'warning' | 'info'
export type EditorTool = 'select' | 'draw'
export type ImportFormat = 'bmson' | 'notelist' | 'bms'
export type ExportFormat = 'bmson' | 'notelist' | 'bms' | 'pm3'

export interface Metadata {
  title: string
  artist: string
  subtitle: string
  game_song_id: string | null
  version: string
  audio_duration: number
  preview_time: number
  import_format: string
  source_name: string | null
  notes: string
}

export interface Lane {
  id: number
  code: string
  display_name: string
  color: string
  hand: 'left' | 'right' | 'either' | 'both'
  kind: 'input' | 'anonymous' | 'auxiliary'
  default_key_sound_id: string | null
  muted: boolean
  extensions: Record<string, unknown>
}

export interface BpmEvent {
  id: string
  pulse: number
  bpm: number
  extensions?: Record<string, unknown>
}

export interface StopEvent {
  id: string
  pulse: number
  duration_pulses: number
  extensions?: Record<string, unknown>
}

export interface BarLine {
  id: string
  pulse: number
  extensions?: Record<string, unknown>
}

export interface TimingMap {
  resolution: number
  initial_bpm: number
  audio_offset_ms: number
  chart_offset_ms: number
  key_sound_offset_ms: number
  mv_offset_ms: number
  bpm_events: BpmEvent[]
  stop_events: StopEvent[]
  bar_lines: BarLine[]
}

export interface Note {
  id: string
  lane_id: number
  pulse: number
  length: number
  key_sound_id: string | null
  volume: number
  playable: boolean
  continues: boolean
  source: string
  notes: string
  extensions?: Record<string, unknown>
}

export interface DifficultyChart {
  id: DifficultyId
  display_name: string
  level: number
  notes: Note[]
  locked: boolean
  description: string
  extensions: Record<string, unknown>
}

export interface KeySoundAsset {
  id: string
  name: string
  filename: string
  lane_ids: number[]
  volume: number
  delay_ms: number
  tags: string[]
  source?: string
  extensions?: Record<string, unknown>
}

export interface AudioAsset {
  id: string
  name: string
  filename: string
  duration: number
  sample_rate: number | null
  extensions?: Record<string, unknown>
}

export interface SongProject {
  schema_version: string
  id: string
  metadata: Metadata
  timing: TimingMap
  lanes: Lane[]
  difficulties: Record<DifficultyId, DifficultyChart>
  audio_assets: AudioAsset[]
  key_sounds: KeySoundAsset[]
  mv_configuration: Record<string, unknown>
  game_specific_data: Record<string, unknown>
  source_files: Array<Record<string, unknown>>
  unknown_data: Record<string, unknown>
  version_history: Array<Record<string, unknown>>
  created_at: string
  updated_at: string
}

export interface ProjectSummary {
  id: string
  title: string
  artist: string
  updated_at: string
  note_count: number
}

export interface ValidationIssue {
  id: string
  severity: Severity
  code: string
  message: string
  difficulty: DifficultyId | null
  note_id: string | null
  pulse: number | null
}

export interface BmsEncodingCandidate {
  encoding: string
  label: string
  preview: string
}

export interface BmsPlayableChannel {
  channel: string
  label: string
  note_count: number
  default_lane: number | null
}

export interface BmsRandomBlock {
  index: number
  maximum: number
  selected: number
}

export interface BmsInspection {
  filename: string
  format: 'bms'
  encoding: string
  encoding_candidates: BmsEncodingCandidate[]
  title: string
  artist: string
  initial_bpm: number
  resolution: number
  measure_count: number
  wav_count: number
  wav_files: Array<{ id: string; filename: string }>
  bmp_count: number
  bmp_files: Array<{ id: string; filename: string; kind: 'image' | 'video' | 'unsupported' }>
  playable_channels: BmsPlayableChannel[]
  random_blocks: BmsRandomBlock[]
  warnings: string[]
}

export interface BmsImportOptions {
  encoding: string
  laneMap: Record<string, number>
  randomValues: Record<number, number>
  preserveUnmapped: boolean
}

export interface Pm3Root {
  id: string
  label: string
  available: boolean
  read_only: boolean
}

export interface Pm3FileEntry {
  name: string
  path: string
  type: 'directory' | 'file' | 'unreadable'
  size: number | null
  modified_at?: number
  format: string
  role: string
}

export interface Pm3DirectoryListing {
  root_id: string
  path: string
  parent: string | null
  truncated: boolean
  entries: Pm3FileEntry[]
}

export interface Pm3HexRow {
  offset: number
  offset_hex: string
  hex: string
  ascii: string
}

export interface Pm3FileInspection {
  root_id: string
  path: string
  name: string
  size: number
  offset: number
  length: number
  format: string
  mime_type: string
  sha256: string | null
  hex_rows: Pm3HexRow[]
  text?: string
  encoding?: string
  has_previous: boolean
  has_next: boolean
}

export interface Pm3CatalogRecord {
  bpm: number
  min_bpm: number
  max_bpm: number
  length: number
  total_hit: number
  max_combo: number
  wav_dir: string
  song_name: string
  singer_name: string
  song_id: number
  singer_id: number
  music_style: number
  hidden: number
  class_id: number
  difficulty: DifficultyId
  level: number
  filename: string
  line_number: number
  root_id: string
  path: string
  available: boolean
}

export interface Pm3Catalog {
  total: number
  offset: number
  limit: number
  warnings: string[]
  records: Pm3CatalogRecord[]
}

export interface Pm3ChartInspection {
  format: 'pm3-chart'
  filename: string
  encoding: string
  encrypted: boolean
  used_cut: boolean
  slot: number
  header: string
  plain_length: number
  sha256: string
  bpm_changes: Array<{ tick: number; pulse: number; bpm: number }>
  rhythm_changes: Array<{ section: number; beats: number; tick: number; pulse: number }>
  track_ids: number[]
  playable_events: number
  note_objects: number
  hold_notes: number
  auxiliary_events: number
  event_count: number
  declared_total_note: number | null
  wav_count: number
  unknown_line_count: number
  warnings: string[]
  text_preview: string
  resources: {
    audio: Array<{ role: string; root_id: string; path: string; exists?: boolean; size?: number }>
    key_sounds: Array<{ role: string; root_id: string; path: string; exists?: boolean; size?: number; wav_index: number; raw_path: string }>
    mv: Array<{ role: string; root_id: string; path: string; exists?: boolean; size?: number }>
  }
  song?: Pm3CatalogRecord
  root_id: string
  path: string
}

export interface Pm3FileRef {
  root_id: string
  path: string
  name?: string
}

export interface Pm3DiffResult {
  left: Pm3FileRef & { size: number; sha256: string | null }
  right: Pm3FileRef & { size: number; sha256: string | null }
  identical: boolean
  compared_bytes: number
  changed_bytes: number
  first_differing_offsets: number[]
  windows: Array<{ offset: number; left: string; right: string }>
  text_diff: string[] | null
  truncated: boolean
}

export interface Pm3ExportTarget {
  id: string
  label: string
  kind: 'staging' | 'deployment'
  path: string
  backup: boolean
}

export interface Pm3ExportFile {
  path: string
  size: number
  md5: string
  sha256: string
}

export interface Pm3ExportTextPreview {
  filename: string
  encoding: string
  text: string
}

export interface Pm3ExportPreview {
  valid: boolean
  filename: string
  song_id: number
  slot: number
  header: string
  warnings: string[]
  stats: Record<string, number | string | boolean>
  files: Pm3ExportFile[]
  target_version: string
  resources: Array<Record<string, unknown>>
  previews: {
    chart: Pm3ChartInspection
    update_list: Pm3ExportTextPreview
    song_list: Pm3ExportTextPreview | null
  }
}

export interface Pm3ExportReport {
  export_id: string
  status: 'staged' | 'published' | 'rolled_back'
  created_at: string
  title: string
  difficulty: DifficultyId
  target_version: string
  target: { id: string; label: string; kind: string; path: string }
  filename: string
  song_id: number
  slot: number
  header: string
  include_song_list: boolean
  files: Pm3ExportFile[]
  resources: Array<Record<string, unknown>>
  warnings: string[]
  stats: Record<string, number | string | boolean>
  round_trip: { passed: boolean; notes_before: number; notes_after: number; events_after: number }
  rollback_available: boolean
  published_at?: string
  rolled_back_at?: string
}
