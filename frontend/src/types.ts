export type DifficultyId = 'easy' | 'normal' | 'hard' | 'special' | 'master'
export type Severity = 'error' | 'warning' | 'info'
export type EditorTool = 'select' | 'draw'
export type ImportFormat = 'bmson' | 'notelist' | 'bms'
export type ExportFormat = 'bmson' | 'notelist' | 'bms' | 'pm3' | 'pm3-version'

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
  pending?: boolean
}

export interface Pm3ExportTextPreview {
  filename: string
  encoding: string
  text: string
}

export interface Pm3ResourceStatus {
  available: boolean
  source: string | null
  size: number | null
  output_path: string
}

export type Pm3ResourceProfile = 'extracted-media-overlay' | 'squashfs-ota'

export interface Pm3ResourcePackage {
  profile: Pm3ResourceProfile
  complete: boolean
  song_id: number
  audio: {
    source_name: string | null
    duration: number | null
    preview_start: number
    preview_duration: number | null
    background: Pm3ResourceStatus
    preview: Pm3ResourceStatus
  }
  mv: {
    id: number
    custom: boolean
    available: boolean
    source_name: string | null
    output_path: string | null
    inspection: {
      signature: string
      version: number
      width: number
      height: number
      frame_rate: number
      frame_count: number
      labels: string[]
      size: number
      sha256: string
      as2_compatible: boolean
    } | null
    error?: string
    mapping: string
    requires_lua_rom_rebuild: boolean
  }
  rom: {
    available: boolean
    bundle: number
    files: string[]
    missing: string[]
    tools: { mksquashfs: string | null; unsquashfs: string | null }
    source: string
  } | null
  warnings: string[]
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
  include_resources: boolean
  mv_id: number
  resource_profile: Pm3ResourceProfile
  resource_package: Pm3ResourcePackage
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
  include_resources: boolean
  mv_id: number
  resource_profile: Pm3ResourceProfile
  resource_package: Pm3ResourcePackage
  files: Pm3ExportFile[]
  resources: Array<Record<string, unknown>>
  warnings: string[]
  stats: Record<string, number | string | boolean>
  round_trip: { passed: boolean; notes_before: number; notes_after: number; events_after: number }
  rollback_available: boolean
  published_at?: string
  rolled_back_at?: string
}

export interface Pm3VersionDifficulty {
  id: DifficultyId
  label: string
  level: number
  notes: number
}

export interface Pm3VersionCandidate {
  project_id: string
  title: string
  artist: string
  song_id: number | null
  slot: number
  mv_id: number
  difficulties: Pm3VersionDifficulty[]
  audio_ready: boolean
  audio: {
    background: Omit<Pm3ResourceStatus, 'output_path'>
    preview: Omit<Pm3ResourceStatus, 'output_path'>
  }
  released?: {
    version_name: string
    song_id: number
    slot: number
    mv_id: number
    difficulties: DifficultyId[]
  } | null
  next_version_name?: string
  updated_at: string
}

export interface Pm3VersionEntry {
  project_id: string
  difficulty: DifficultyId
  song_id: number
  slot: number
  mv_id: number
}

export interface Pm3VersionSong {
  song_id: number
  project_id: string
  title: string
  artist: string
  mv_id: number
  audio_ready: boolean
  charts: Array<{
    difficulty: DifficultyId
    difficulty_label: string
    level: number
    slot: number
    filename: string
    note_objects: number
    event_count: number
  }>
}

export interface Pm3VersionPreview {
  valid: boolean
  version_name: string
  cumulative?: boolean
  lineage?: {
    cumulative: boolean
    base_export_id: string | null
    base_version_name: string | null
    required_song_count: number
    required_chart_count: number
  }
  songs: Pm3VersionSong[]
  stats: {
    song_count: number
    chart_count: number
    bundle_count: number
    bundles: number[]
    note_objects: number
    event_count: number
    custom_key_sound_count?: number
    custom_mv_count?: number
  }
  rom: {
    available: boolean
    song_ids: number[]
    bundles: number[]
    files: string[]
    missing: string[]
    tools: { mksquashfs: string | null; unsquashfs: string | null }
    source: string
  }
  files: Pm3ExportFile[]
  warnings: string[]
  previews: {
    update_list: Pm3ExportTextPreview
    song_list: Pm3ExportTextPreview
  }
}

export interface Pm3VersionReport {
  export_id: string
  kind: 'pm3-version'
  status: 'staged'
  created_at: string
  filename: string
  version_name: string
  cumulative?: boolean
  lineage?: Pm3VersionPreview['lineage']
  target_version: string
  target: { id: string; label: string; kind: string; path: string }
  songs: Pm3VersionSong[]
  stats: Pm3VersionPreview['stats']
  rom: Pm3VersionPreview['rom']
  resource_profile: 'squashfs-ota'
  include_resources: true
  include_song_list: true
  files: Pm3ExportFile[]
  warnings: string[]
  rollback_available: false
}

export interface Pm3ExportSummary {
  export_id: string
  status: string
  created_at: string
  filename: string
  version_name?: string
  kind?: string
  title?: string
  resource_profile?: Pm3ResourceProfile
}

export interface Pm3OtaBaselineStatus {
  status: 'present' | 'missing' | 'unavailable'
  root_id: string | null
  path: string | null
  size: number | null
}

export interface Pm3OtaAuditOperation {
  line: number
  action: 'r' | 'd'
  path: string
  expected_md5: string | null
  actual_md5: string | null
  size: number | null
  verified: boolean
  format: 'song-list' | 'chart' | 'squashfs' | 'binary'
  format_valid: boolean | null
  format_detail?: string
  baseline: Pm3OtaBaselineStatus
  effect: 'replace' | 'create' | 'delete' | 'noop' | 'unknown'
}

export interface Pm3OtaAudit {
  export_id: string
  version_name: string
  kind: string
  created_at: string | null
  valid: boolean
  read_only: true
  activation_timestamp: number
  activation_time: string | null
  operation_count: number
  counts: {
    replace: number
    create: number
    delete: number
    noop: number
    unknown: number
    verified: number
  }
  operations: Pm3OtaAuditOperation[]
  song_list: null | {
    valid: boolean
    encoding?: string
    row_count?: number
    file_end_line?: number | null
    rows_after_end?: string[]
    filenames: string[]
    warnings?: string[]
    error?: string
  }
  rom: { count: number; valid: boolean; paths: string[] }
  unmanaged_files: string[]
  unmanaged_count: number
  errors: string[]
  warnings: string[]
}

export interface Pm3OtaChain {
  valid: boolean
  read_only: true
  export_ids: string[]
  versions: Array<{
    export_id: string
    version_name: string
    valid: boolean
    operation_count: number
  }>
  counts: {
    versions: number
    operations: number
    overrides: number
    deletes: number
    final_files: number
  }
  transitions: Array<{
    order: number
    export_id: string
    version_name: string
    path: string
    change: 'unchanged' | 'deleted' | 'restored' | 'create' | 'replace' | 'unknown' | 'overridden'
    before_md5: string | null
    after_md5: string | null
  }>
  song_list_changes: Array<{
    export_id: string
    version_name: string
    added: string[]
    removed: string[]
  }>
  errors: string[]
  warnings: string[]
}

export interface Pm3OtaMirrorOptions {
  generation?: number
  installedVersion?: number
  installedEdition?: number
  downloadedVersion?: number
  downloadedEdition?: number
  verifyPayloads?: boolean
}

export interface Pm3OtaMirrorPackage {
  name: string
  kind: 'version' | 'edition'
  version: number
  edition: number
  timestamp: number | null
  activation_time: string | null
  due: boolean | null
  operation_count: number
  counts: {
    replace: number
    add: number
    delete: number
    verified_payloads: number
    missing_payloads: number
    md5_mismatches: number
    unmanaged_files: number
  }
  mismatches: Array<{
    path: string
    expected_md5: string
    actual_md5: string
  }>
  missing_payloads: string[]
  unmanaged_files: string[]
  planned: boolean
  cumulative: boolean | null
  valid: boolean
  errors: string[]
  warnings: string[]
}

export interface Pm3OtaMirrorAudit {
  valid: boolean
  read_only: true
  verify_payloads: boolean
  integrity_verified: boolean
  root: string
  patch_root: string
  generation: number
  installed: { version: number; edition: number }
  downloaded: { version: number; edition: number }
  config: {
    machine: null | { generation: number; version: number; edition: number; area: number }
    update: null | { version: number; edition: number }
  }
  available: {
    versions: number[]
    editions: Record<string, number[]>
    catalog_version_gaps: number[]
  }
  plan: {
    valid: boolean
    version_steps: string[]
    edition_step: string | null
    steps: string[]
    missing_versions: number[]
    errors: string[]
  }
  edition_chains: Array<{
    version: number
    editions: number[]
    cumulative: boolean
    breaks: Array<{
      previous: string
      current: string
      missing_count: number
      missing_paths: string[]
    }>
  }>
  counts: {
    packages: number
    versions: number
    editions: number
    operations: number
    replace: number
    add: number
    delete: number
    verified_payloads: number
    missing_payloads: number
    md5_mismatches: number
    invalid_packages: number
    cumulative_breaks: number
  }
  packages: Pm3OtaMirrorPackage[]
  errors: string[]
  warnings: string[]
}
