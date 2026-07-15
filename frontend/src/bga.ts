import { createTimingIndex, pulseToSeconds } from './timing'
import type { SongProject } from './types'

export type BgaEventKind = 'base' | 'layer' | 'poor'
export type BgaMediaKind = 'image' | 'video' | 'unsupported'

export interface BgaResource {
  id: string
  filename: string
  kind: BgaMediaKind
  projectId: string
  path: string
  previewPath: string
  exists: boolean
  previewError: string
}

export interface BgaEvent {
  key: string
  kind: BgaEventKind
  bmpId: string
  pulse: number
  time: number
  line: number
}

export interface BmsBgaData {
  resources: Map<string, BgaResource>
  events: Record<BgaEventKind, BgaEvent[]>
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function mediaKind(filename: string): BgaMediaKind {
  const suffix = filename.toLowerCase().match(/\.[^.\/\\]+$/)?.[0] ?? ''
  if (['.bmp', '.gif', '.jpeg', '.jpg', '.png', '.webp'].includes(suffix)) return 'image'
  if (['.avi', '.mkv', '.mov', '.mp4', '.mpeg', '.mpg', '.ogv', '.webm'].includes(suffix)) return 'video'
  return 'unsupported'
}

export function readBmsBga(project: SongProject): BmsBgaData | null {
  const root = record(project.mv_configuration.bms_bga)
  if (!root) return null
  const definitions = record(root.bmp_defs) ?? {}
  const rawAssets = record(root.assets) ?? {}
  const resources = new Map<string, BgaResource>()
  for (const [rawId, rawFilename] of Object.entries(definitions)) {
    const id = rawId.toUpperCase()
    const asset = record(rawAssets[id])
    const resource = record(asset?.resource)
    const filename = typeof asset?.filename === 'string'
      ? asset.filename
      : typeof rawFilename === 'string' ? rawFilename : String(rawFilename)
    const rawKind = asset?.kind
    const kind = rawKind === 'image' || rawKind === 'video' || rawKind === 'unsupported'
      ? rawKind
      : mediaKind(filename)
    resources.set(id, {
      id,
      filename,
      kind,
      projectId: typeof resource?.project_id === 'string' ? resource.project_id : project.id,
      path: typeof resource?.path === 'string' ? resource.path : '',
      previewPath: typeof resource?.preview_path === 'string' ? resource.preview_path : '',
      exists: resource?.exists === true,
      previewError: typeof resource?.preview_error === 'string' ? resource.preview_error : '',
    })
  }

  const timingIndex = createTimingIndex(project.timing)
  const rawEvents = record(root.events) ?? {}
  const events = Object.fromEntries((['base', 'layer', 'poor'] as const).map((kind) => {
    const values = Array.isArray(rawEvents[kind]) ? rawEvents[kind] : []
    const parsed = values.flatMap((value, index) => {
      const item = record(value)
      const bmpId = typeof item?.bmp_id === 'string' ? item.bmp_id.toUpperCase() : ''
      const pulse = Number(item?.pulse)
      if (!bmpId || !Number.isFinite(pulse) || pulse < 0) return []
      const line = Number.isFinite(Number(item?.line)) ? Number(item?.line) : 0
      return [{
        key: `bga:${kind}:${line}:${bmpId}:${index}`,
        kind,
        bmpId,
        pulse,
        time: pulseToSeconds(project, pulse, timingIndex) + project.timing.mv_offset_ms / 1000,
        line,
      } satisfies BgaEvent]
    }).sort((left, right) => left.time - right.time || left.line - right.line || left.key.localeCompare(right.key))
    return [kind, parsed]
  })) as Record<BgaEventKind, BgaEvent[]>

  return { resources, events }
}

export function activeBgaEvent(events: BgaEvent[], position: number): BgaEvent | null {
  let low = 0
  let high = events.length - 1
  let result: BgaEvent | null = null
  while (low <= high) {
    const middle = Math.floor((low + high) / 2)
    if (events[middle].time <= position + 0.000001) {
      result = events[middle]
      low = middle + 1
    } else {
      high = middle - 1
    }
  }
  return result
}

export function bgaResourceUrl(resource: BgaResource): string | null {
  if (!resource.exists || !resource.projectId) return null
  const rawPath = resource.previewPath || resource.path
  const path = rawPath.replaceAll('\\', '/')
  if (!path || path.split('/').includes('..')) return null
  const query = new URLSearchParams({ path })
  return `/api/projects/${encodeURIComponent(resource.projectId)}/bga-resource?${query}`
}
