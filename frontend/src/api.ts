import type {
  BmsImportOptions,
  BmsInspection,
  DifficultyId,
  ProjectSummary,
  Pm3Catalog,
  Pm3ChartInspection,
  Pm3DiffResult,
  Pm3DirectoryListing,
  Pm3FileInspection,
  Pm3FileRef,
  Pm3Root,
  Pm3ExportPreview,
  Pm3ExportReport,
  Pm3ExportTarget,
  SongProject,
  ValidationIssue,
} from './types'

async function parseError(response: Response): Promise<Error> {
  try {
    const body = await response.json() as { detail?: unknown }
    const detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    return new Error(detail || `请求失败 (${response.status})`)
  } catch {
    return new Error(`请求失败 (${response.status})`)
  }
}

async function jsonRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) throw await parseError(response)
  return response.json() as Promise<T>
}

async function download(url: string, fallbackName: string): Promise<void> {
  const response = await fetch(url)
  if (!response.ok) throw await parseError(response)
  const blob = await response.blob()
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  const asciiName = disposition.match(/filename="([^"]+)"/)?.[1]
  const name = encodedName ? decodeURIComponent(encodedName) : (asciiName ?? fallbackName)
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = name
  anchor.click()
  URL.revokeObjectURL(objectUrl)
}

export function bmsResourcePath(file: File): string {
  const raw = (typeof file.webkitRelativePath === 'string' ? file.webkitRelativePath : '').replaceAll('\\', '/')
  if (!raw) return file.name
  const parts = raw.split('/').filter(Boolean)
  return parts.length > 1 ? parts.slice(1).join('/') : file.name
}

export const api = {
  listProjects: () => jsonRequest<ProjectSummary[]>('/api/projects'),

  getProject: (id: string) => jsonRequest<SongProject>(`/api/projects/${id}`),

  createProject: (title: string, artist: string, initialBpm: number) => jsonRequest<SongProject>('/api/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, artist, initial_bpm: initialBpm }),
  }),

  saveProject: (project: SongProject) => jsonRequest<SongProject>(`/api/projects/${project.id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(project),
  }),

  importBmson: (file: File, difficulty: DifficultyId) => {
    const body = new FormData()
    body.append('file', file)
    return jsonRequest<SongProject>(`/api/import/bmson?difficulty=${difficulty}`, { method: 'POST', body })
  },

  importNoteList: (file: File, difficulty: DifficultyId) => {
    const body = new FormData()
    body.append('file', file)
    return jsonRequest<SongProject>(`/api/import/notelist?difficulty=${difficulty}`, { method: 'POST', body })
  },

  inspectBms: (file: File, encoding?: string) => {
    const body = new FormData()
    body.append('file', file)
    if (encoding) body.append('encoding', encoding)
    return jsonRequest<BmsInspection>('/api/import/bms/inspect', { method: 'POST', body })
  },

  importBms: (file: File, difficulty: DifficultyId, options: BmsImportOptions, resources: File[] = []) => {
    const body = new FormData()
    body.append('file', file)
    for (const resource of resources) body.append('resources', resource, resource.name)
    body.append('resource_paths', JSON.stringify(resources.map(bmsResourcePath)))
    body.append('encoding', options.encoding)
    body.append('lane_map', JSON.stringify(options.laneMap))
    body.append('random_values', JSON.stringify(options.randomValues))
    body.append('preserve_unmapped', String(options.preserveUnmapped))
    return jsonRequest<SongProject>(`/api/import/bms?difficulty=${difficulty}`, { method: 'POST', body })
  },

  pm3Roots: () => jsonRequest<Pm3Root[]>('/api/pm3/roots'),

  pm3Tree: (rootId: string, path = '') => {
    const query = new URLSearchParams({ root: rootId, path })
    return jsonRequest<Pm3DirectoryListing>(`/api/pm3/tree?${query}`)
  },

  pm3File: (rootId: string, path: string, offset = 0, length = 4096) => {
    const query = new URLSearchParams({ root: rootId, path, offset: String(offset), length: String(length) })
    return jsonRequest<Pm3FileInspection>(`/api/pm3/file?${query}`)
  },

  pm3Catalog: (search = '', offset = 0, limit = 1000) => {
    const query = new URLSearchParams({ search, offset: String(offset), limit: String(limit) })
    return jsonRequest<Pm3Catalog>(`/api/pm3/catalog?${query}`)
  },

  pm3Chart: (rootId: string, path: string) => {
    const query = new URLSearchParams({ root: rootId, path })
    return jsonRequest<Pm3ChartInspection>(`/api/pm3/chart?${query}`)
  },

  pm3Diff: (left: Pm3FileRef, right: Pm3FileRef) => jsonRequest<Pm3DiffResult>('/api/pm3/diff', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ left, right }),
  }),

  importPm3: (rootId: string, path: string, difficulty: DifficultyId) => jsonRequest<SongProject>('/api/import/pm3', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ root_id: rootId, path, difficulty }),
  }),

  pm3ExportTargets: () => jsonRequest<Pm3ExportTarget[]>('/api/pm3/export-targets'),

  pm3ExportPreview: (projectId: string, difficulty: DifficultyId, slot: number, includeSongList: boolean) => {
    const query = new URLSearchParams({
      difficulty,
      slot: String(slot),
      include_song_list: String(includeSongList),
    })
    return jsonRequest<Pm3ExportPreview>(`/api/projects/${projectId}/export/pm3/preview?${query}`)
  },

  exportPm3: (
    projectId: string,
    difficulty: DifficultyId,
    targetId: string,
    slot: number,
    includeSongList: boolean,
  ) => jsonRequest<Pm3ExportReport>(`/api/projects/${projectId}/export/pm3`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ difficulty, target_id: targetId, slot, include_song_list: includeSongList }),
  }),

  downloadPm3: (exportId: string) => download(
    `/api/pm3/exports/${exportId}/download`,
    `pm3-${exportId}.zip`,
  ),

  rollbackPm3: (exportId: string) => jsonRequest<Pm3ExportReport>(`/api/pm3/exports/${exportId}/rollback`, {
    method: 'POST',
  }),

  validateProject: (id: string, difficulty: DifficultyId) =>
    jsonRequest<ValidationIssue[]>(`/api/projects/${id}/validate?difficulty=${difficulty}`, { method: 'POST' }),

  bmsCompatibility: (id: string, difficulty: DifficultyId) =>
    jsonRequest<ValidationIssue[]>(`/api/projects/${id}/compatibility/bms?difficulty=${difficulty}`),

  async exportBmson(project: SongProject, difficulty: DifficultyId): Promise<void> {
    await api.saveProject(project)
    await download(
      `/api/projects/${project.id}/export/bmson?difficulty=${difficulty}`,
      `${project.metadata.title}-${difficulty}.bmson`,
    )
  },

  async exportNoteList(project: SongProject, difficulty: DifficultyId, tpb = 48): Promise<void> {
    await api.saveProject(project)
    await download(
      `/api/projects/${project.id}/export/notelist?difficulty=${difficulty}&tpb=${tpb}`,
      `${project.metadata.title}-${difficulty}.notelist.json`,
    )
  },

  async exportBms(project: SongProject, difficulty: DifficultyId, encoding = 'utf-8'): Promise<void> {
    await api.saveProject(project)
    await download(
      `/api/projects/${project.id}/export/bms?difficulty=${difficulty}&encoding=${encodeURIComponent(encoding)}`,
      `${project.metadata.title}-${difficulty}.bms`,
    )
  },
}
