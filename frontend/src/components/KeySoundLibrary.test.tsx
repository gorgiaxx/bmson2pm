// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import { createDemoProject } from '../demo'
import { useEditorStore } from '../store'
import type { KeySoundAsset } from '../types'
import { KeySoundLibrary } from './KeySoundLibrary'

function manualAsset(id = 'manual-rim'): KeySoundAsset {
  return {
    id,
    name: 'Rim',
    filename: 'rim.wav',
    lane_ids: [],
    volume: 1,
    delay_ms: 0,
    tags: [],
    source: 'manual',
    extensions: {
      editor: {
        resource: {
          project_id: 'key-library-project',
          path: `key-sounds/${id}.wav`,
          exists: true,
        },
      },
    },
  }
}

describe('KeySoundLibrary', () => {
  beforeEach(() => {
    const project = createDemoProject('key-library-project')
    project.key_sounds = [manualAsset()]
    useEditorStore.getState().setProject(project)
    useEditorStore.getState().setActiveLane(1)
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('edits, auditions, and assigns a Lane default while protecting references', () => {
    const trigger = vi.fn()
    render(<KeySoundLibrary onTriggerKeySound={trigger} />)

    fireEvent.change(screen.getByRole('textbox', { name: '名称' }), { target: { value: 'Tight Rim' } })
    expect(useEditorStore.getState().project.key_sounds[0].name).toBe('Tight Rim')
    fireEvent.click(screen.getByRole('button', { name: '试听' }))
    expect(trigger).toHaveBeenCalledWith('manual-rim')

    fireEvent.change(screen.getByRole('combobox', { name: '默认音色' }), { target: { value: 'manual-rim' } })
    expect(useEditorStore.getState().project.lanes[0].default_key_sound_id).toBe('manual-rim')
    expect((screen.getByRole('button', { name: '删除 Tight Rim' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('uploads audio into the current project library', async () => {
    const uploaded = manualAsset('uploaded-hit')
    uploaded.name = 'Uploaded Hit'
    vi.spyOn(api, 'uploadKeySound').mockResolvedValue(uploaded)
    render(<KeySoundLibrary onTriggerKeySound={() => undefined} />)

    const file = new File(['RIFF'], 'uploaded.wav', { type: 'audio/wav' })
    fireEvent.change(screen.getByLabelText('上传 Key 音文件'), { target: { files: [file] } })

    await waitFor(() => expect(api.uploadKeySound).toHaveBeenCalledWith('key-library-project', file))
    await waitFor(() => expect(useEditorStore.getState().project.key_sounds.some(
      (asset) => asset.id === 'uploaded-hit',
    )).toBe(true))
    expect(screen.getAllByText('Uploaded Hit').length).toBeGreaterThan(0)
  })

  it('deletes an unreferenced uploaded resource from storage and the project', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(api, 'deleteKeySound').mockResolvedValue(undefined)
    render(<KeySoundLibrary onTriggerKeySound={() => undefined} />)

    fireEvent.click(screen.getByRole('button', { name: '删除 Rim' }))

    await waitFor(() => expect(api.deleteKeySound).toHaveBeenCalledWith(
      'key-library-project',
      'manual-rim',
      'key-sounds/manual-rim.wav',
    ))
    await waitFor(() => expect(useEditorStore.getState().project.key_sounds).toHaveLength(0))
  })

  it('restores project metadata when storage deletion fails', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(api, 'deleteKeySound').mockRejectedValue(new Error('storage unavailable'))
    render(<KeySoundLibrary onTriggerKeySound={() => undefined} />)

    fireEvent.click(screen.getByRole('button', { name: '删除 Rim' }))

    expect((await screen.findByRole('alert')).textContent).toContain('storage unavailable')
    expect(useEditorStore.getState().project.key_sounds.map((asset) => asset.id)).toContain('manual-rim')
  })
})
