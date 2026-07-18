// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import type {
  Pm3ExportSummary,
  Pm3OtaAudit,
  Pm3OtaChain,
  Pm3OtaMirrorAudit,
} from '../types'
import { Pm3OtaAuditView } from './Pm3OtaAuditView'

const reports: Pm3ExportSummary[] = [
  {
    export_id: 'export-11', status: 'staged', created_at: '2026-07-16T02:00:00Z',
    filename: 'ver011', version_name: 'ver011', kind: 'pm3-version',
  },
  {
    export_id: 'export-10', status: 'staged', created_at: '2026-07-16T01:00:00Z',
    filename: 'ver010', version_name: 'ver010', kind: 'pm3-version',
  },
]

const audit: Pm3OtaAudit = {
  export_id: 'export-11', version_name: 'ver011', kind: 'pm3-version',
  created_at: '2026-07-16T02:00:00Z', valid: true, read_only: true,
  activation_timestamp: 0, activation_time: null, operation_count: 1,
  counts: { replace: 1, create: 0, delete: 0, noop: 0, unknown: 0, verified: 1 },
  operations: [{
    line: 2, action: 'r', path: 'ROMS/sound.rom', expected_md5: 'A'.repeat(32),
    actual_md5: 'A'.repeat(32), size: 1024, verified: true, format: 'squashfs',
    format_valid: true, format_detail: 'SquashFS magic', effect: 'replace',
    baseline: { status: 'present', root_id: 'game', path: 'ROMS/sound.rom', size: 900 },
  }],
  song_list: {
    valid: true, encoding: 'cp950', row_count: 212, file_end_line: 214,
    rows_after_end: [], filenames: ['p001_easy'], warnings: [],
  },
  rom: { count: 1, valid: true, paths: ['ROMS/sound.rom'] },
  unmanaged_files: [], unmanaged_count: 0, errors: [],
  warnings: ['不会复制、删除或挂载文件'],
}

const chain: Pm3OtaChain = {
  valid: true, read_only: true, export_ids: ['export-10', 'export-11'],
  versions: [
    { export_id: 'export-10', version_name: 'ver010', valid: true, operation_count: 1 },
    { export_id: 'export-11', version_name: 'ver011', valid: true, operation_count: 1 },
  ],
  counts: { versions: 2, operations: 2, overrides: 1, deletes: 0, final_files: 1 },
  transitions: [{
    order: 2, export_id: 'export-11', version_name: 'ver011', path: 'ROMS/sound.rom',
    change: 'overridden', before_md5: 'B'.repeat(32), after_md5: 'A'.repeat(32),
  }],
  song_list_changes: [
    { export_id: 'export-10', version_name: 'ver010', added: ['p042_easy'], removed: [] },
    { export_id: 'export-11', version_name: 'ver011', added: ['p043_easy'], removed: [] },
  ],
  errors: [], warnings: ['版本链仅在内存中模拟'],
}

const mirror: Pm3OtaMirrorAudit = {
  valid: true, read_only: true, verify_payloads: true, integrity_verified: true,
  root: '/offline/pm3', patch_root: 'patch', generation: 1,
  installed: { version: 1, edition: 0 }, downloaded: { version: 2, edition: 1 },
  config: {
    machine: { generation: 1, version: 1, edition: 0, area: 1 },
    update: { version: 2, edition: 1 },
  },
  available: { versions: [1, 2], editions: { 2: [1] }, catalog_version_gaps: [] },
  plan: {
    valid: true, version_steps: ['ver002'], edition_step: 'edt002001',
    steps: ['ver002', 'edt002001'], missing_versions: [], errors: [],
  },
  edition_chains: [{ version: 2, editions: [1], cumulative: true, breaks: [] }],
  counts: {
    packages: 2, versions: 1, editions: 1, operations: 3, replace: 1, add: 2,
    delete: 0, verified_payloads: 3, missing_payloads: 0, md5_mismatches: 0,
    invalid_packages: 0, cumulative_breaks: 0,
  },
  packages: [{
    name: 'ver002', kind: 'version', version: 2, edition: 0, timestamp: 0,
    activation_time: null, due: true, operation_count: 1,
    counts: {
      replace: 1, add: 0, delete: 0, verified_payloads: 1,
      missing_payloads: 0, md5_mismatches: 0, unmanaged_files: 0,
    },
    mismatches: [], missing_payloads: [], unmanaged_files: [], planned: true,
    cumulative: null, valid: true, errors: [], warnings: [],
  }, {
    name: 'edt002001', kind: 'edition', version: 2, edition: 1, timestamp: 0,
    activation_time: null, due: true, operation_count: 2,
    counts: {
      replace: 0, add: 2, delete: 0, verified_payloads: 2,
      missing_payloads: 0, md5_mismatches: 0, unmanaged_files: 0,
    },
    mismatches: [], missing_payloads: [], unmanaged_files: [], planned: true,
    cumulative: null, valid: true, errors: [], warnings: [],
  }],
  errors: [], warnings: ['镜像审计只读取本地目录'],
}

describe('Pm3OtaAuditView', () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('audits the latest local package and simulates selected versions in install order', async () => {
    vi.spyOn(api, 'pm3Exports').mockResolvedValue(reports)
    const auditMock = vi.spyOn(api, 'pm3ExportAudit').mockResolvedValue(audit)
    const chainMock = vi.spyOn(api, 'pm3AuditChain').mockResolvedValue(chain)

    render(<Pm3OtaAuditView />)

    await waitFor(() => expect(auditMock).toHaveBeenCalledWith('export-11'))
    expect((await screen.findAllByText('VERIFIED')).length).toBeGreaterThan(0)
    expect(screen.getByText('ROMS/sound.rom')).toBeTruthy()

    fireEvent.click(screen.getByRole('checkbox', { name: '版本链选择 ver010' }))
    fireEvent.click(screen.getByRole('button', { name: '模拟版本链' }))

    await waitFor(() => expect(chainMock).toHaveBeenCalledWith(['export-10', 'export-11']))
    expect(await screen.findByText('CONSISTENT')).toBeTruthy()
    expect(screen.getByText('ver010 → ver011')).toBeTruthy()
  })

  it('scans the trusted FTP mirror with optional full MD5 verification', async () => {
    vi.spyOn(api, 'pm3Exports').mockResolvedValue(reports)
    vi.spyOn(api, 'pm3ExportAudit').mockResolvedValue(audit)
    const mirrorMock = vi.spyOn(api, 'pm3OtaMirror').mockResolvedValue(mirror)

    render(<Pm3OtaAuditView />)
    await screen.findByText('ROMS/sound.rom')
    fireEvent.click(screen.getByRole('tab', { name: 'FTP 镜像' }))
    fireEvent.click(screen.getByRole('checkbox', { name: /完整 MD5/ }))
    fireEvent.click(screen.getByRole('button', { name: '扫描镜像' }))

    await waitFor(() => expect(mirrorMock).toHaveBeenCalledWith({
      generation: undefined,
      installedVersion: undefined,
      installedEdition: undefined,
      downloadedVersion: undefined,
      downloadedEdition: undefined,
      verifyPayloads: true,
    }))
    expect((await screen.findAllByText('VERIFIED')).length).toBeGreaterThan(0)
    expect(screen.getByText('patch · GENERATION 1')).toBeTruthy()
    expect(screen.getByText('edt002001')).toBeTruthy()
  })
})
