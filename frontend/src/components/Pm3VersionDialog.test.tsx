// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import type {
  Pm3VersionCandidate,
  Pm3VersionEntry,
  Pm3VersionPreview,
  Pm3VersionReport,
} from '../types'
import { Pm3VersionDialog } from './Pm3VersionDialog'

const candidates: Pm3VersionCandidate[] = [
  {
    project_id: 'alpha', title: 'Alpha', artist: 'Artist A', song_id: 42,
    slot: 2, mv_id: 14, audio_ready: true, updated_at: '2026-07-16T00:00:00Z',
    difficulties: [{ id: 'easy', label: '初级', level: 3, notes: 120 }],
    audio: {
      background: { available: true, source: 'project:bg', size: 100 },
      preview: { available: true, source: 'project:pre', size: 80 },
    },
  },
  {
    project_id: 'beta', title: 'Beta', artist: 'Artist B', song_id: null,
    slot: 3, mv_id: 18, audio_ready: true, updated_at: '2026-07-15T00:00:00Z',
    difficulties: [{ id: 'hard', label: '高级', level: 7, notes: 320 }],
    audio: {
      background: { available: true, source: 'project:bg', size: 120 },
      preview: { available: true, source: 'project:pre', size: 90 },
    },
  },
]

function versionPreview(entries: Pm3VersionEntry[]): Pm3VersionPreview {
  const songs = entries.map((entry) => {
    const candidate = candidates.find((item) => item.project_id === entry.project_id) as Pm3VersionCandidate
    return {
      song_id: entry.song_id, project_id: entry.project_id, title: candidate.title,
      artist: candidate.artist, mv_id: entry.mv_id, audio_ready: true,
      charts: [{
        difficulty: entry.difficulty, difficulty_label: candidate.difficulties[0].label,
        level: candidate.difficulties[0].level, slot: entry.slot,
        filename: `p${String(entry.song_id).padStart(3, '0')}_${entry.difficulty}.enc`,
        note_objects: candidate.difficulties[0].notes,
        event_count: candidate.difficulties[0].notes,
      }],
    }
  })
  return {
    valid: true,
    version_name: 'ver010',
    songs,
    stats: {
      song_count: entries.length, chart_count: entries.length,
      bundle_count: entries.length, bundles: entries.map((entry) => entry.song_id > 210 ? 7 : 2),
      note_objects: 440, event_count: 440,
    },
    rom: {
      available: true, song_ids: entries.map((entry) => entry.song_id), bundles: [2, 7],
      files: ['ROMS/lua_script.rom', 'ROMS/sound.rom'], missing: [],
      tools: { mksquashfs: 'mksquashfs', unsquashfs: 'unsquashfs' },
      source: 'game:ROMS + game:media（只读）',
    },
    files: [
      { path: 'ver010/ROMS/lua_script.rom', size: 0, md5: 'PENDING', sha256: 'PENDING', pending: true },
      { path: 'ver010/update.lst', size: 64, md5: 'A'.repeat(32), sha256: 'B'.repeat(64) },
    ],
    warnings: ['不会连接 FTP 或修改 update.cfg'],
    previews: {
      update_list: { filename: 'ver010/update.lst', encoding: 'ascii', text: '0\r\nr, ROMS/lua_script.rom, PENDING\r\n' },
      song_list: { filename: 'ver010/rewrite/script_download/SongList.enc', encoding: 'cp950', text: 'p042_easy\r\n#---- FILE END ----#\r\n' },
    },
  }
}

describe('Pm3VersionDialog', () => {
  afterEach(() => vi.restoreAllMocks())

  it('previews selected projects and exports one shared version directory', async () => {
    vi.spyOn(api, 'pm3VersionCandidates').mockResolvedValue(candidates)
    const previewMock = vi.spyOn(api, 'pm3VersionPreview').mockImplementation(
      async (_versionName, entries) => versionPreview(entries),
    )
    const report = {
      ...versionPreview([
        { project_id: 'alpha', difficulty: 'easy', song_id: 42, slot: 2, mv_id: 14 },
        { project_id: 'beta', difficulty: 'hard', song_id: 211, slot: 3, mv_id: 18 },
      ]),
      export_id: 'version-1', kind: 'pm3-version', status: 'staged',
      created_at: '2026-07-16T00:00:00Z', filename: 'ver010',
      target_version: 'PM3 offline ver010',
      target: { id: 'staging', label: '安全导出目录', kind: 'staging', path: '/tmp' },
      resource_profile: 'squashfs-ota', include_resources: true,
      include_song_list: true, rollback_available: false,
    } satisfies Pm3VersionReport
    const exportMock = vi.spyOn(api, 'exportPm3Version').mockResolvedValue(report)
    const onComplete = vi.fn()

    render(
      <Pm3VersionDialog
        currentProjectId="alpha"
        currentDifficulty="easy"
        onClose={() => undefined}
        onComplete={onComplete}
      />,
    )

    await waitFor(() => expect(previewMock).toHaveBeenCalledWith('ver010', [{
      project_id: 'alpha', difficulty: 'easy', song_id: 42, slot: 2, mv_id: 14,
    }]))
    fireEvent.click(screen.getByRole('checkbox', { name: '选择 Beta' }))
    await waitFor(() => expect(previewMock).toHaveBeenLastCalledWith('ver010', [
      { project_id: 'alpha', difficulty: 'easy', song_id: 42, slot: 2, mv_id: 14 },
      { project_id: 'beta', difficulty: 'hard', song_id: 211, slot: 3, mv_id: 18 },
    ]))
    expect((await screen.findAllByText(/ROMS\/lua_script\.rom/)).length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: '构建版本目录' }))
    await waitFor(() => expect(exportMock).toHaveBeenCalled())
    await waitFor(() => expect(onComplete).toHaveBeenCalledWith(report))
    expect(screen.getByText('版本目录已生成')).toBeTruthy()
  })

  it('includes every non-empty difficulty of a selected song and allows removing one chart', async () => {
    const multiDifficulty = [{
      ...candidates[0],
      difficulties: [
        ...candidates[0].difficulties,
        { id: 'hard' as const, label: '高级', level: 8, notes: 360 },
      ],
    }]
    vi.spyOn(api, 'pm3VersionCandidates').mockResolvedValue(multiDifficulty)
    const previewMock = vi.spyOn(api, 'pm3VersionPreview').mockImplementation(
      async (_versionName, entries) => versionPreview(entries),
    )

    render(
      <Pm3VersionDialog
        currentProjectId="alpha"
        currentDifficulty="easy"
        onClose={() => undefined}
        onComplete={() => undefined}
      />,
    )

    await waitFor(() => expect(previewMock).toHaveBeenCalledWith('ver010', [
      { project_id: 'alpha', difficulty: 'easy', song_id: 42, slot: 2, mv_id: 14 },
      { project_id: 'alpha', difficulty: 'hard', song_id: 42, slot: 2, mv_id: 14 },
    ]))

    fireEvent.click(screen.getByRole('checkbox', { name: 'Alpha 高级' }))
    await waitFor(() => expect(previewMock).toHaveBeenLastCalledWith('ver010', [
      { project_id: 'alpha', difficulty: 'easy', song_id: 42, slot: 2, mv_id: 14 },
    ]))
  })

  it('automatically carries forward and locks charts from the previous version', async () => {
    const cumulativeCandidates: Pm3VersionCandidate[] = [
      {
        ...candidates[0],
        next_version_name: 'ver011',
        released: {
          version_name: 'ver010',
          song_id: 42,
          slot: 2,
          mv_id: 14,
          difficulties: ['easy'],
        },
      },
      { ...candidates[1], next_version_name: 'ver011' },
    ]
    vi.spyOn(api, 'pm3VersionCandidates').mockResolvedValue(cumulativeCandidates)
    const previewMock = vi.spyOn(api, 'pm3VersionPreview').mockImplementation(
      async (_versionName, entries) => versionPreview(entries),
    )

    render(
      <Pm3VersionDialog
        currentProjectId="beta"
        currentDifficulty="hard"
        onClose={() => undefined}
        onComplete={() => undefined}
      />,
    )

    await waitFor(() => expect(previewMock).toHaveBeenLastCalledWith('ver011', [
      { project_id: 'alpha', difficulty: 'easy', song_id: 42, slot: 2, mv_id: 14 },
      { project_id: 'beta', difficulty: 'hard', song_id: 211, slot: 3, mv_id: 18 },
    ]))
    expect(screen.getByText('HISTORY')).toBeTruthy()
    expect((screen.getAllByRole('checkbox', { name: '选择 Alpha' }).at(-1) as HTMLInputElement).disabled).toBe(true)
    expect((screen.getAllByRole('checkbox', { name: 'Alpha 初级' }).at(-1) as HTMLInputElement).disabled).toBe(true)
    expect((screen.getAllByRole('spinbutton', { name: 'Alpha 曲目序号' }).at(-1) as HTMLInputElement).disabled).toBe(true)
  })
})
