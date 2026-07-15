import { useEffect, useRef } from 'react'
import { api } from '../api'
import { useEditorStore } from '../store'

const LOCAL_KEY = 'bmson2pm:last-project'

export function loadLocalProject(): unknown {
  try {
    const raw = localStorage.getItem(LOCAL_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function useAutosave(enabled: boolean): void {
  const project = useEditorStore((state) => state.project)
  const saveStatus = useEditorStore((state) => state.saveStatus)
  const setSaveStatus = useEditorStore((state) => state.setSaveStatus)
  const editTransaction = useEditorStore((state) => state.editTransaction)
  const lastSaved = useRef('')

  useEffect(() => {
    if (editTransaction) return
    localStorage.setItem(LOCAL_KEY, JSON.stringify(project))
    if (!enabled || saveStatus !== 'dirty') return
    const serialized = JSON.stringify(project)
    if (serialized === lastSaved.current) return
    const timer = window.setTimeout(async () => {
      setSaveStatus('saving')
      try {
        const saved = await api.saveProject(project)
        lastSaved.current = JSON.stringify(saved)
        setSaveStatus('saved', new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }))
      } catch (error) {
        setSaveStatus('error', error instanceof Error ? error.message : '自动保存失败')
      }
    }, 700)
    return () => window.clearTimeout(timer)
  }, [editTransaction, enabled, project, saveStatus, setSaveStatus])
}
