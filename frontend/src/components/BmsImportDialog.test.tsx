// @vitest-environment jsdom

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { BmsImportDialog } from './BmsImportDialog'
import type { BmsInspection } from '../types'

const inspection: BmsInspection = {
  filename: 'fixture.bms',
  format: 'bms',
  encoding: 'cp932',
  encoding_candidates: [
    { encoding: 'cp932', label: 'CP932 / Shift_JIS', preview: '#TITLE テスト曲' },
    { encoding: 'utf-8', label: 'UTF-8', preview: '#TITLE test' },
  ],
  title: 'テスト曲',
  artist: 'IGS',
  initial_bpm: 150,
  resolution: 240,
  measure_count: 2,
  wav_count: 2,
  wav_files: [{ id: '01', filename: '01.wav' }, { id: '02', filename: '02.wav' }],
  bmp_count: 1,
  bmp_files: [{ id: '01', filename: 'movie.avi', kind: 'video' }],
  playable_channels: [
    { channel: '11', label: 'P1 Key 1 (#11)', note_count: 4, default_lane: 1 },
    { channel: '12', label: 'P1 Key 2 (#12)', note_count: 3, default_lane: 2 },
  ],
  random_blocks: [{ index: 1, maximum: 2, selected: 1 }],
  warnings: ['未知指令 #FOO 已原样保留'],
}

describe('BmsImportDialog', () => {
  it('selects encoding, lane mapping and RANDOM branch', () => {
    const storage = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      removeItem: (key: string) => storage.delete(key),
      clear: () => storage.clear(),
    })
    const onEncodingChange = vi.fn()
    const onConfirm = vi.fn()
    render(
      <BmsImportDialog
        inspection={inspection}
        chartFiles={[new File([''], 'fixture.bms')]}
        selectedChartPath="fixture.bms"
        resourceFiles={[new File(['ogg'], '01.ogg'), new File(['avi'], 'movie.avi')]}
        busy={false}
        onClose={() => undefined}
        onEncodingChange={onEncodingChange}
        onChartChange={() => undefined}
        onResourceFiles={() => undefined}
        onConfirm={onConfirm}
      />,
    )

    expect(screen.getByRole('dialog', { name: 'テスト曲' })).toBeTruthy()
    expect(screen.getByText('1 / 2')).toBeTruthy()
    expect(screen.getByText('1 / 1')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('BMS 文本编码'), { target: { value: 'utf-8' } })
    expect(onEncodingChange).toHaveBeenCalledWith('utf-8')

    fireEvent.change(screen.getByLabelText('左小鼓来源通道'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('鼓缘同时击打来源通道'), { target: { value: '11' } })
    fireEvent.change(screen.getByLabelText('#1'), { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: '导入到当前难度' }))

    expect(onConfirm).toHaveBeenCalledWith({
      encoding: 'cp932',
      laneMap: { '11': 3, '12': 2 },
      randomValues: { 1: 2 },
      preserveUnmapped: true,
    })
  })
})
