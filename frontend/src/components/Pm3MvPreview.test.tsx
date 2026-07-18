// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createPm3MvPlayer,
  type RufflePlayerApi,
  type RufflePlayerElement,
} from '../pm3MvRuffle'
import { Pm3MvPreview } from './Pm3MvPreview'

vi.mock('../pm3MvRuffle', () => ({
  createPm3MvPlayer: vi.fn(),
}))

function fakePlayer(exposeController: boolean) {
  const players: Array<{ api: RufflePlayerApi; element: RufflePlayerElement }> = []
  vi.mocked(createPm3MvPlayer).mockImplementation(async () => {
    const element = document.createElement('div') as unknown as RufflePlayerElement
    const api = {
      load: vi.fn().mockImplementation(async () => {
        queueMicrotask(() => element.dispatchEvent(new Event('loadeddata')))
      }),
      callExternalInterface: vi.fn(),
      resume: vi.fn(),
      suspend: vi.fn(),
      isPlaying: true,
    } satisfies RufflePlayerApi
    element.ruffle = () => api
    if (exposeController && players.length === 0) element.MVLoad = vi.fn()
    players.push({ api, element })
    return element
  })
  return players
}

describe('Pm3MvPreview', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('loads the PM3 controller and exposes state controls when a host bridge exists', async () => {
    const players = fakePlayer(true)
    render(<Pm3MvPreview projectId="demo project" mvId={7} available />)

    expect(await screen.findByText('CONTROL')).toBeTruthy()
    const { api } = players[0]
    expect(api.load).toHaveBeenCalledWith(expect.objectContaining({
      url: '/api/projects/demo%20project/pm3/mv-preview/mvctrl/mvctrl.swf',
    }))
    expect(api.callExternalInterface).toHaveBeenCalledWith('MVLoad', 7)

    fireEvent.click(screen.getByRole('button', { name: 'MIDDLE' }))
    expect(api.callExternalInterface).toHaveBeenCalledWith('MVState', 1)
    fireEvent.click(screen.getByTitle('触发 Heavy'))
    expect(api.callExternalInterface).toHaveBeenCalledWith('MVHeavy')
    fireEvent.click(screen.getByTitle('切换连续状态'))
    expect(api.callExternalInterface).toHaveBeenCalledWith('MVCont', true)
    fireEvent.click(screen.getByTitle('暂停 MV'))
    expect(api.suspend).toHaveBeenCalled()
    fireEvent.click(screen.getByTitle('播放 MV'))
    expect(api.resume).toHaveBeenCalled()
  })

  it('falls back to the selected MV SWF when Scaleform host callbacks are absent', async () => {
    const players = fakePlayer(false)
    render(<Pm3MvPreview projectId="demo" mvId={20} available />)

    expect(await screen.findByText('VISUAL')).toBeTruthy()
    await waitFor(() => expect(players).toHaveLength(2))
    const controllerApi = players[0].api
    const directApi = players[1].api
    expect(controllerApi.load).toHaveBeenCalledWith(expect.objectContaining({
      url: '/api/projects/demo/pm3/mv-preview/mvctrl/mvctrl.swf',
    }))
    expect(controllerApi.suspend).toHaveBeenCalled()
    expect(directApi.load).toHaveBeenCalledWith(expect.objectContaining({
      url: expect.stringMatching(
        /^\/api\/projects\/demo\/pm3\/mv-preview\/mv\/mv20\.swf\?state=full&preview=\d+$/,
      ),
    }))
    await waitFor(() => expect(directApi.load).toHaveBeenCalledTimes(2))
    const full = screen.getByRole('button', { name: 'FULL' })
    expect((full as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(full)
    await waitFor(() => expect(directApi.load).toHaveBeenLastCalledWith(expect.objectContaining({
      url: expect.stringMatching(
        /^\/api\/projects\/demo\/pm3\/mv-preview\/mv\/mv20\.swf\?state=full&preview=\d+$/,
      ),
    })))
    expect((screen.getByTitle('触发 Heavy') as HTMLButtonElement).disabled).toBe(true)
  })
})
