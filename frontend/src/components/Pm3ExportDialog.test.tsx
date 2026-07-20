// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import { createDemoProject } from '../demo'
import type { Pm3ExportPreview, Pm3ExportReport, Pm3ResourcePackage, Pm3ResourceProfile } from '../types'
import { Pm3ExportDialog } from './Pm3ExportDialog'

vi.mock('./Pm3MvPreview', () => ({
  Pm3MvPreview: () => <div data-testid="pm3-mv-preview" />,
}))

function preview(
  songId: number,
  includeSongList: boolean,
  profile: Pm3ResourceProfile = 'extracted-media-overlay',
  guestAvailable = true,
  musicStyle = 0,
): Pm3ExportPreview {
  const stem = `p${String(songId).padStart(3, '0')}_hard`
  const slot = songId % 10
  const resourcePackage: Pm3ResourcePackage = {
    profile, complete: false, song_id: songId,
    audio: {
      source_name: null, duration: null, preview_start: 0, preview_duration: null,
      background: { available: false, source: null, size: null, output_path: `media/sound/BG/BG_${String(songId).padStart(3, '0')}.ogg` },
      preview: { available: false, source: null, size: null, output_path: `media/sound/preview/p${String(songId).padStart(3, '0')}.wav` },
    },
    mv: {
      id: 0, custom: false, available: true, source_name: null, output_path: null,
      inspection: null, mapping: `StageConfig.MV[${songId}] = 0`,
      requires_lua_rom_rebuild: profile !== 'squashfs-ota',
    },
    rom: profile === 'squashfs-ota' ? {
      available: true, bundle: 4,
      files: ['ROMS/lua_script.rom', 'ROMS/sound.rom', 'ROMS/SOUND_BG4.rom', 'ROMS/SOUND_PRE4.rom'],
      missing: [], tools: { mksquashfs: 'mksquashfs', unsquashfs: 'unsquashfs' },
      source: 'game:ROMS + game:media（只读）',
    } : null,
    warnings: [],
  }
  return {
    valid: true,
    filename: `${stem}.enc`,
    song_id: songId,
    slot,
    header: '0x13552068',
    warnings: [],
    stats: { note_objects: 2, event_count: 3, round_trip_verified: true },
    files: [
      { path: `rewrite/script_download/${stem}.enc`, size: 128, md5: 'A'.repeat(32), sha256: 'B'.repeat(64) },
      { path: 'update.lst', size: 96, md5: 'C'.repeat(32), sha256: 'D'.repeat(64) },
    ],
    target_version: 'MVP',
    resources: [],
    include_resources: false,
    music_style: musicStyle,
    guest_available: guestAvailable,
    mv_id: 0,
    resource_profile: profile,
    resource_package: resourcePackage,
    previews: {
      chart: {
        format: 'pm3-chart', filename: `${stem}.enc`, encoding: 'ascii', encrypted: true,
        used_cut: false, slot, header: '0x13552068', plain_length: 128,
        sha256: 'B'.repeat(64), bpm_changes: [{ tick: 0, pulse: 0, bpm: 120 }],
        rhythm_changes: [{ section: 1, beats: 4, tick: 0, pulse: 0 }],
        track_ids: [0, 16], playable_events: 2, note_objects: 2, hold_notes: 0,
        auxiliary_events: 1, event_count: 3, declared_total_note: 2, wav_count: 2,
        unknown_line_count: 0, warnings: [], text_preview: `TRACK 0\nEVENT 12 0 127 0 0 0\nWAV 001 ./${String(songId).padStart(3, '0')}/BG.wav`,
        resources: { audio: [], key_sounds: [], mv: [] },
        root_id: 'export-preview', path: `rewrite/script_download/${stem}.enc`,
      },
      update_list: {
        filename: 'update.lst', encoding: 'ascii',
        text: `123\r\nr, rewrite/script_download/${stem}.enc, ABCD\r\n`,
      },
      song_list: includeSongList ? {
        filename: 'rewrite/script_download/SongList.enc', encoding: 'cp950',
        text: `12000,12000,12000,48,2,2,${String(songId).padStart(3, '0')},Demo,Artist,${songId},0,0,0,2,5,${stem}`,
      } : null,
    },
  }
}

describe('Pm3ExportDialog', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('selects a reserved song ID and previews chart, update.lst, and plaintext SongList', async () => {
    const project = createDemoProject('pm3-export-project')
    project.metadata.game_song_id = 'p001'
    const previewMock = vi.spyOn(api, 'pm3ExportPreview').mockImplementation(
      async (_projectId, _difficulty, songId, _slot, includeSongList, _includeResources, musicStyle, guestAvailable, _mvId, profile) => (
        preview(songId, includeSongList, profile, guestAvailable, musicStyle)
      ),
    )
    vi.spyOn(api, 'pm3ExportTargets').mockResolvedValue([{
      id: 'staging', label: '安全导出目录', kind: 'staging', path: '/tmp/exports', backup: false,
    }])
    const exportMock = vi.spyOn(api, 'exportPm3').mockResolvedValue({
      export_id: 'export-1', status: 'staged', created_at: '2026-07-16T00:00:00Z',
      title: project.metadata.title, difficulty: 'hard', target_version: 'MVP',
      target: { id: 'staging', label: '安全导出目录', kind: 'staging', path: '/tmp/exports' },
      filename: 'p150_hard.enc', song_id: 150, slot: 0, header: '0x13552068',
      include_song_list: true, include_resources: false, music_style: 1,
      guest_available: false, mv_id: 0,
      resource_profile: 'extracted-media-overlay',
      resource_package: preview(150, true).resource_package,
      files: [], resources: [], warnings: [], stats: {},
      round_trip: { passed: true, notes_before: 2, notes_after: 2, events_after: 3 },
      rollback_available: false,
    } satisfies Pm3ExportReport)
    const onComplete = vi.fn()
    const onProjectChange = vi.fn()

    render(<Pm3ExportDialog project={project} difficulty="hard" onClose={() => undefined} onComplete={onComplete} onProjectChange={onProjectChange} />)
    await waitFor(() => expect(previewMock).toHaveBeenCalledWith(project.id, 'hard', 134, 4, false, false, 0, true, 0, 'extracted-media-overlay'))

    fireEvent.click(screen.getByRole('button', { name: '离线 ROM' }))
    await waitFor(() => expect(previewMock).toHaveBeenCalledWith(project.id, 'hard', 134, 4, false, false, 0, true, 0, 'squashfs-ota'))
    fireEvent.click(screen.getByRole('button', { name: '文件覆盖' }))

    fireEvent.change(screen.getByRole('combobox', { name: '曲目序号' }), { target: { value: '150' } })
    await waitFor(() => expect(previewMock).toHaveBeenCalledWith(project.id, 'hard', 150, 0, false, false, 0, true, 0, 'extracted-media-overlay'))

    fireEvent.click(screen.getByRole('tab', { name: 'update.lst' }))
    expect((await screen.findAllByText(/rewrite\/script_download\/p150_hard\.enc/)).length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('checkbox', { name: '重建 SongList.enc' }))
    await waitFor(() => expect(previewMock).toHaveBeenCalledWith(project.id, 'hard', 150, 0, true, false, 0, true, 0, 'extracted-media-overlay'))
    const songListTab = await screen.findByRole('tab', { name: 'SongList 明文' })
    await waitFor(() => expect((songListTab as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(songListTab)
    expect((await screen.findAllByText(/p150_hard/)).length).toBeGreaterThan(0)
    expect((screen.getByRole('combobox', { name: 'Key slot' }) as HTMLSelectElement).value).toBe('0')

    fireEvent.change(screen.getByRole('combobox', { name: '音乐分类' }), { target: { value: '1' } })
    await waitFor(() => expect(previewMock).toHaveBeenCalledWith(project.id, 'hard', 150, 0, true, false, 1, true, 0, 'extracted-media-overlay'))

    fireEvent.click(screen.getByRole('checkbox', { name: '游客直接开放' }))
    await waitFor(() => expect(previewMock).toHaveBeenCalledWith(project.id, 'hard', 150, 0, true, false, 1, false, 0, 'extracted-media-overlay'))

    fireEvent.click(screen.getByRole('button', { name: '生成安全包' }))
    await waitFor(() => expect(exportMock).toHaveBeenCalledWith(
      project.id, 'hard', 'staging', 150, 0, true, false, 1, false, 0, 'extracted-media-overlay',
    ))
    await waitFor(() => expect(onComplete).toHaveBeenCalled())
    expect(onProjectChange).toHaveBeenCalledWith(expect.objectContaining({
      metadata: expect.objectContaining({ game_song_id: 'p150' }),
      game_specific_data: expect.objectContaining({
        pm3_song_id: 150, pm3_slot: 0, pm3_music_style: 1,
        pm3_guest_available: false,
      }),
    }))
  })

  it('assigns a non-zero custom song ID and deterministic key slot by default', async () => {
    const project = createDemoProject('pm3-new-custom-project')
    vi.spyOn(api, 'pm3ExportTargets').mockResolvedValue([])
    const previewMock = vi.spyOn(api, 'pm3ExportPreview').mockImplementation(
      async (_projectId, _difficulty, songId, _slot, includeSongList, _resources, musicStyle, guestAvailable, _mvId, profile) => (
        preview(songId, includeSongList, profile, guestAvailable, musicStyle)
      ),
    )

    render(
      <Pm3ExportDialog
        project={project}
        difficulty="hard"
        onClose={() => undefined}
        onComplete={() => undefined}
        onProjectChange={() => undefined}
      />,
    )

    await waitFor(() => expect(previewMock).toHaveBeenCalledWith(
      project.id, 'hard', 134, 4, false, false, 0, true, 0, 'extracted-media-overlay',
    ))
    expect((screen.getByRole('combobox', { name: '曲目序号' }) as HTMLSelectElement).value).toBe('134')
    expect((screen.getByRole('combobox', { name: 'Key slot' }) as HTMLSelectElement).value).toBe('4')
  })

  it('uploads a custom SWF and selects its MV ID', async () => {
    const project = createDemoProject('pm3-custom-mv-project')
    vi.spyOn(api, 'pm3ExportTargets').mockResolvedValue([{
      id: 'staging', label: '安全导出目录', kind: 'staging', path: '/tmp/exports', backup: false,
    }])
    const previewMock = vi.spyOn(api, 'pm3ExportPreview').mockImplementation(
      async (_projectId, _difficulty, songId, _slot, includeSongList, _resources, musicStyle, guestAvailable, mvId, profile) => {
        const result = preview(songId, includeSongList, profile, guestAvailable, musicStyle)
        result.mv_id = mvId
        result.resource_package.mv.id = mvId
        result.resource_package.mv.custom = mvId >= 20
        result.resource_package.mv.available = true
        return result
      },
    )
    vi.spyOn(api, 'saveProject').mockResolvedValue(project)
    const updated = structuredClone(project)
    updated.mv_configuration.pm3_mv_id = 20
    updated.game_specific_data.pm3_package = {
      mv: { id: 20, source_name: 'custom.swf' },
    }
    const prepareMv = vi.spyOn(api, 'preparePm3Mv').mockResolvedValue(updated)
    const onProjectChange = vi.fn()
    const rendered = render(
      <Pm3ExportDialog
        project={project}
        difficulty="hard"
        onClose={() => undefined}
        onComplete={() => undefined}
        onProjectChange={onProjectChange}
      />,
    )
    const input = rendered.container.querySelector<HTMLInputElement>(
      'input[accept=".swf,application/x-shockwave-flash"]',
    )
    expect(input).not.toBeNull()
    const file = new File(['FWS-test'], 'custom.swf', {
      type: 'application/x-shockwave-flash',
    })
    fireEvent.change(input as HTMLInputElement, { target: { files: [file] } })
    fireEvent.click(screen.getByRole('button', { name: '校验并保存 MV' }))

    await waitFor(() => expect(prepareMv).toHaveBeenCalledWith(project.id, file, 20))
    expect(onProjectChange).toHaveBeenCalledWith(updated)
    await waitFor(() => expect(previewMock).toHaveBeenCalledWith(
      project.id, 'hard', 134, 4, false, false, 0, true, 20, 'extracted-media-overlay',
    ))
  })
})
