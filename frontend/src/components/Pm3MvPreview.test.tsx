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

function fakePlayer() {
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

  it('loads the selected PM3 MV directly without showing the MV0 controller', async () => {
    const players = fakePlayer()
    render(<Pm3MvPreview projectId="demo project" mvId={7} available />)

    expect(await screen.findByText('VISUAL')).toBeTruthy()
    const { api } = players[0]
    expect(api.load).toHaveBeenCalledWith(expect.objectContaining({
      url: expect.stringMatching(
        /^\/api\/projects\/demo%20project\/pm3\/mv-preview\/mv\/mv7\.swf\?state=full&preview=\d+$/,
      ),
    }))
    expect(api.load).not.toHaveBeenCalledWith(expect.objectContaining({
      url: expect.stringContaining('/mvctrl/'),
    }))
    fireEvent.click(screen.getByTitle('暂停 MV'))
    expect(api.suspend).toHaveBeenCalled()
    fireEvent.click(screen.getByTitle('播放 MV'))
    expect(api.resume).toHaveBeenCalled()
  })

  it('replaces the player for state and MV changes', async () => {
    const players = fakePlayer()
    const rendered = render(<Pm3MvPreview projectId="demo" mvId={6} available />)

    expect(await screen.findByText('VISUAL')).toBeTruthy()
    await waitFor(() => expect(players).toHaveLength(1))
    const directApi = players[0].api
    expect(directApi.load).toHaveBeenCalledWith(expect.objectContaining({
      url: expect.stringMatching(
        /^\/api\/projects\/demo\/pm3\/mv-preview\/mv\/mv6\.swf\?state=full&preview=\d+$/,
      ),
    }))
    await waitFor(() => expect(directApi.load).toHaveBeenCalledTimes(2))
    const middle = screen.getByRole('button', { name: 'MIDDLE' })
    expect((middle as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(middle)
    await waitFor(() => expect(players).toHaveLength(2))
    const middleApi = players[1].api
    expect(directApi.suspend).toHaveBeenCalled()
    await waitFor(() => expect(middleApi.load).toHaveBeenCalledWith(expect.objectContaining({
      url: expect.stringMatching(
        /^\/api\/projects\/demo\/pm3\/mv-preview\/mv\/mv6\.swf\?state=middle&preview=\d+$/,
      ),
    })))
    expect(directApi.load).toHaveBeenCalledTimes(2)
    rendered.rerender(<Pm3MvPreview projectId="demo" mvId={19} available />)
    await waitFor(() => expect(players).toHaveLength(3))
    const nextMvApi = players[2].api
    await waitFor(() => expect(nextMvApi.load).toHaveBeenCalledWith(expect.objectContaining({
      url: expect.stringMatching(
        /^\/api\/projects\/demo\/pm3\/mv-preview\/mv\/mv19\.swf\?state=full&preview=\d+$/,
      ),
    })))
  })
})
