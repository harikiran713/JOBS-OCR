/**
 * The only bridge between the sandboxed renderer and Electron. Each function maps
 * to one narrowly-scoped IPC handler; no generic filesystem or IPC access is exposed.
 */
import { contextBridge, ipcRenderer, webUtils } from 'electron'

type BackendState =
  | { status: 'starting' }
  | { status: 'ready'; url: string; token: string }
  | { status: 'error'; message: string; logHint: string }

const api = {
  getBackendState: (): Promise<BackendState> => ipcRenderer.invoke('backend:state'),
  restartBackend: (): Promise<BackendState> => ipcRenderer.invoke('backend:restart'),
  onBackendState: (cb: (state: BackendState) => void): (() => void) => {
    const listener = (_e: unknown, state: BackendState): void => cb(state)
    ipcRenderer.on('backend:state', listener)
    return () => ipcRenderer.removeListener('backend:state', listener)
  },
  appInfo: (): Promise<{ version: string; platform: string; dataDir: string; isPackaged: boolean }> =>
    ipcRenderer.invoke('app:info'),
  pickAndImport: (projectId: string): Promise<unknown> => ipcRenderer.invoke('documents:pick', projectId),
  importDropped: (projectId: string, files: File[]): Promise<unknown> =>
    ipcRenderer.invoke(
      'documents:importDropped',
      projectId,
      files.map((f) => webUtils.getPathForFile(f))
    ),
  exportExcel: (projectId: string, options: unknown, suggestedName: string): Promise<unknown> =>
    ipcRenderer.invoke('export:excel', projectId, options, suggestedName),
  exportPms: (projectId: string, template: 'ocr' | 'capital', description: 'long' | 'short', baseName: string, documentId?: string | null): Promise<unknown> =>
    ipcRenderer.invoke('export:pms', projectId, template, description, baseName, documentId ?? null),
  showExport: (path: string): Promise<boolean> => ipcRenderer.invoke('shell:showExport', path),
  openDataFolder: (): Promise<void> => ipcRenderer.invoke('shell:openDataFolder'),
  openLogs: (): Promise<void> => ipcRenderer.invoke('shell:openLogs'),
  setTheme: (theme: 'light' | 'dark' | 'system'): Promise<void> => ipcRenderer.invoke('theme:set', theme)
}

contextBridge.exposeInMainWorld('mmi', api)

export type MmiBridge = typeof api
