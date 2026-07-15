// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Pm3ResearchDialog } from './Pm3ResearchDialog'

function response(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

describe('Pm3ResearchDialog', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('inspects a catalog chart and imports it', async () => {
    const record = {
      bpm: 17000, min_bpm: 17000, max_bpm: 17000, length: 3504,
      total_hit: 158, max_combo: 168, wav_dir: '001', song_name: '搖擺頭',
      singer_name: '原唱：張韶涵', song_id: 1, singer_id: 1, music_style: 0,
      hidden: 0, class_id: 0, difficulty: 'easy', level: 3, filename: 'p001_easy',
      line_number: 2, root_id: 'game', path: 'media/script_AES/p001_easy.enccut', available: true,
    }
    const inspection = {
      format: 'pm3-chart', filename: 'p001_easy.enccut', encoding: 'ascii', encrypted: true,
      used_cut: true, slot: 1, header: '0x533d235e', plain_length: 5956, sha256: 'a'.repeat(64),
      bpm_changes: [{ tick: 0, pulse: 0, bpm: 170 }],
      rhythm_changes: [{ section: 1, beats: 4, tick: 0, pulse: 0 }],
      track_ids: [0, 1, 2, 5, 16], playable_events: 150, note_objects: 146,
      hold_notes: 4, auxiliary_events: 4, event_count: 154, declared_total_note: 150,
      wav_count: 15, unknown_line_count: 0, warnings: [], text_preview: 'ChangeBPM 0 17000',
      resources: { audio: [{ role: 'background-audio', root_id: 'game', path: 'media/sound/BG/BG_001.ogg' }], key_sounds: [], mv: [] },
      song: record, root_id: 'game', path: record.path,
    }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.startsWith('/api/pm3/roots')) return response([{ id: 'game', label: '游戏只读目录', available: true, read_only: true }])
      if (url.startsWith('/api/pm3/tree')) return response({ root_id: 'game', path: '', parent: null, truncated: false, entries: [] })
      if (url.startsWith('/api/pm3/catalog')) return response({ total: 1, offset: 0, limit: 1000, warnings: [], records: [record] })
      if (url.startsWith('/api/pm3/chart')) return response(inspection)
      if (url === '/api/import/pm3' && init?.method === 'POST') return response({ id: 'imported-project' })
      throw new Error(`Unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const onImported = vi.fn()

    render(<Pm3ResearchDialog difficulty="hard" onClose={() => undefined} onImported={onImported} />)
    fireEvent.click(await screen.findByRole('button', { name: /搖擺頭/ }))
    expect(await screen.findByText('PM3 / SLOT 1')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '导入谱面' }))

    await waitFor(() => expect(onImported).toHaveBeenCalledWith({ id: 'imported-project' }, 'p001_easy'))
    const importCall = fetchMock.mock.calls.find(([url]) => String(url) === '/api/import/pm3')
    expect(JSON.parse(String(importCall?.[1]?.body))).toEqual({
      root_id: 'game', path: record.path, difficulty: 'easy',
    })
  })
})
