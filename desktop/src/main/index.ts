import { existsSync, statSync } from 'node:fs'
import { basename, extname, join } from 'node:path'
import { BrowserWindow, Menu, app, dialog, ipcMain, nativeTheme, shell } from 'electron'
import { Backend } from './backend'

// Electron is sometimes launched from tools that set this; it would turn the app into plain Node.
delete process.env.ELECTRON_RUN_AS_NODE

const isDev = !app.isPackaged
let mainWindow: BrowserWindow | null = null
let backend: Backend

if (!app.requestSingleInstanceLock()) {
  app.quit()
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 660,
    show: false,
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#0b1220' : '#f5f6f8',
    title: 'Marine Maintenance Intelligence',
    // Packaged builds take the icon from the executable; development uses the source icon.
    icon: app.isPackaged ? undefined : join(app.getAppPath(), 'build', 'icon.ico'),
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      spellcheck: false
    }
  })
  mainWindow.once('ready-to-show', () => mainWindow?.show())

  // The renderer may never navigate away or open windows; external links are not followed.
  mainWindow.webContents.on('will-navigate', (event, url) => {
    const allowed = isDev && process.env.ELECTRON_RENDERER_URL && url.startsWith(process.env.ELECTRON_RENDERER_URL)
    if (!allowed) event.preventDefault()
  })
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  mainWindow.webContents.session.setPermissionRequestHandler((_wc, _permission, callback) => callback(false))

  if (isDev && process.env.ELECTRON_RENDERER_URL) {
    void mainWindow.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    void mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

type ImportResult = { results: Array<{ path: string; filename: string; status: string; message?: string }> }

function validPdfPaths(paths: unknown): string[] {
  if (!Array.isArray(paths)) return []
  return paths.filter((p): p is string => {
    if (typeof p !== 'string' || extname(p).toLowerCase() !== '.pdf') return false
    try {
      return existsSync(p) && statSync(p).isFile()
    } catch {
      return false
    }
  })
}

function assertId(value: unknown): string {
  if (typeof value !== 'string' || !/^[0-9a-f-]{36}$/i.test(value)) throw new Error('Invalid identifier')
  return value
}

function registerIpc(): void {
  ipcMain.handle('backend:state', () => backend.getState())
  ipcMain.handle('backend:restart', async () => {
    await backend.restart()
    return backend.getState()
  })

  ipcMain.handle('app:info', () => ({
    version: app.getVersion(),
    platform: process.platform,
    dataDir: backend.dataDir,
    isPackaged: app.isPackaged
  }))

  // File access happens only here, in response to explicit user actions.
  ipcMain.handle('documents:pick', async (_e, projectId: unknown) => {
    const id = assertId(projectId)
    const res = await dialog.showOpenDialog(mainWindow!, {
      title: 'Import manuals',
      properties: ['openFile', 'multiSelections'],
      filters: [{ name: 'PDF manuals', extensions: ['pdf'] }]
    })
    if (res.canceled || res.filePaths.length === 0) return null
    return backend.request<ImportResult>(`/api/projects/${id}/documents/import`, {
      method: 'POST',
      body: { paths: validPdfPaths(res.filePaths) }
    })
  })

  ipcMain.handle('documents:importDropped', async (_e, projectId: unknown, paths: unknown) => {
    const id = assertId(projectId)
    const valid = validPdfPaths(paths)
    const rejected = (Array.isArray(paths) ? paths : [])
      .filter((p): p is string => typeof p === 'string' && !valid.includes(p))
      .map((p) => ({ path: p, filename: basename(p), status: 'rejected', message: 'Only PDF files can be imported' }))
    if (valid.length === 0) return { results: rejected }
    const res = await backend.request<ImportResult>(`/api/projects/${id}/documents/import`, {
      method: 'POST',
      body: { paths: valid }
    })
    return { results: [...res.results, ...rejected] }
  })

  ipcMain.handle('export:excel', async (_e, projectId: unknown, options: unknown, suggestedName: unknown) => {
    const id = assertId(projectId)
    const name = typeof suggestedName === 'string' ? suggestedName.replace(/[\\/:*?"<>|]+/g, '_') : 'maintenance-checklist'
    const res = await dialog.showSaveDialog(mainWindow!, {
      title: 'Export maintenance checklist',
      defaultPath: join(app.getPath('documents'), `${name}.xlsx`),
      filters: [{ name: 'Excel workbook', extensions: ['xlsx'] }]
    })
    if (res.canceled || !res.filePath) return null
    const body = { ...(options as object), file_path: res.filePath }
    return backend.request(`/api/projects/${id}/export`, { method: 'POST', body })
  })

  ipcMain.handle('export:pms', async (_e, projectId: unknown, template: unknown, description: unknown, baseName: unknown, documentId: unknown) => {
    const id = assertId(projectId)
    const docId = documentId == null || documentId === '' ? null : assertId(documentId)
    if ((template !== 'ocr' && template !== 'capital') || (description !== 'long' && description !== 'short')) {
      throw new Error('Invalid export options')
    }
    const base = typeof baseName === 'string' && baseName.trim() ? baseName.replace(/[\\/:*?"<>|]+/g, '_') : 'manual'
    const suffix = template === 'ocr' ? 'OCRTemplate' : 'CapitalTemplate'
    const res = await dialog.showSaveDialog(mainWindow!, {
      title: template === 'ocr' ? 'Export OCR Template' : 'Export Capital Template',
      defaultPath: join(app.getPath('documents'), `${base}_${suffix}.xlsx`),
      filters: [{ name: 'Excel workbook', extensions: ['xlsx'] }]
    })
    if (res.canceled || !res.filePath) return null
    return backend.request(`/api/projects/${id}/export/pms`, {
      method: 'POST',
      body: { template, description, file_path: res.filePath, document_id: docId }
    })
  })

  ipcMain.handle('shell:showExport', async (_e, path: unknown) => {
    if (typeof path !== 'string') return false
    // Only reveal files that the application itself exported.
    const history = await backend.request<Array<{ file_path: string }>>('/api/exports')
    if (!history.some((h) => h.file_path === path) || !existsSync(path)) return false
    shell.showItemInFolder(path)
    return true
  })

  ipcMain.handle('shell:openDataFolder', async () => {
    await shell.openPath(backend.dataDir)
  })

  ipcMain.handle('shell:openLogs', async () => {
    await shell.openPath(join(backend.dataDir, 'logs'))
  })

  ipcMain.handle('theme:set', (_e, theme: unknown) => {
    if (theme === 'light' || theme === 'dark' || theme === 'system') nativeTheme.themeSource = theme
  })
}

app.whenReady().then(() => {
  Menu.setApplicationMenu(null)
  backend = new Backend()
  registerIpc()
  backend.onChange((state) => {
    for (const w of BrowserWindow.getAllWindows()) w.webContents.send('backend:state', state)
  })
  backend.start()
  createWindow()

  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.focus()
    }
  })
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

let quitting = false
app.on('before-quit', (event) => {
  if (quitting) return
  quitting = true
  event.preventDefault()
  void backend.stop().finally(() => app.quit())
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
