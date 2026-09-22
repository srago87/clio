// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import { app, BrowserWindow, ipcMain, shell } from 'electron'
import { join } from 'path'
import { homedir } from 'os'
import { readdir, readFile, stat } from 'fs/promises'
import { existsSync, readFileSync } from 'fs'
import chokidar, { type FSWatcher } from 'chokidar'

// Clio's display and the Clio server are two separate processes on the same
// machine. Clio writes its per-run auth token to a local file on start; the
// server's local-only listener (see server/serve.py) is always plain
// ws://127.0.0.1:8766, no TLS, so this doesn't need Tailscale or any tunnel —
// see the project-clio memory for why that matters.
interface ClioConfig {
  wsUrl: string
  token: string
}

function readClioConfig(): ClioConfig | null {
  const clioDir = process.env['CLIO_DIR'] ?? join(__dirname, '../../..')
  const tokenPath = join(clioDir, 'server', '.session_token')
  if (!existsSync(tokenPath)) return null
  const token = readFileSync(tokenPath, 'utf-8').trim()
  if (!token) return null
  const wsUrl = process.env['CLIO_WS_URL'] ?? 'ws://127.0.0.1:8766/ws/display'
  return { wsUrl, token }
}

let mainWindow: BrowserWindow | null = null
let watcher: FSWatcher | null = null
let fileWatcher: FSWatcher | null = null

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1240,
    height: 820,
    minWidth: 860,
    minHeight: 560,
    show: false,
    autoHideMenuBar: true,
    title: 'Clio',
    backgroundColor: '#0f1115',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  })

  mainWindow.on('ready-to-show', () => mainWindow?.show())
  mainWindow.on('closed', () => {
    mainWindow = null
  })

  if (process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

interface RawItem {
  name: string
  path: string
  isDir: boolean
  size: number
  mtime: number
  ext: string
  hidden: boolean
}

async function listDir(dirPath: string): Promise<{
  path: string
  parent: string | null
  items: RawItem[]
}> {
  const entries = await readdir(dirPath, { withFileTypes: true })
  const items = await Promise.all(
    entries.map(async (e): Promise<RawItem> => {
      const full = join(dirPath, e.name)
      let size = 0
      let mtime = 0
      let isDir = e.isDirectory()
      try {
        const s = await stat(full)
        size = s.size
        mtime = s.mtimeMs
        isDir = s.isDirectory()
      } catch {
        // broken symlink or unreadable — still list the entry
      }
      const dot = e.name.lastIndexOf('.')
      const ext = !isDir && dot > 0 ? e.name.slice(dot + 1).toLowerCase() : ''
      return { name: e.name, path: full, isDir, size, mtime, ext, hidden: e.name.startsWith('.') }
    })
  )
  const parent = dirPath === '/' ? null : join(dirPath, '..')
  return { path: dirPath, parent, items }
}

function stopWatching(): void {
  watcher?.close()
  watcher = null
}

function stopWatchingFile(): void {
  fileWatcher?.close()
  fileWatcher = null
}

ipcMain.handle('fs:home', () => homedir())

ipcMain.handle('fs:list', (_e, dirPath: string) => listDir(dirPath))

ipcMain.handle('fs:readFile', (_e, filePath: string) => readFile(filePath, 'utf-8'))

ipcMain.handle('fs:watch', (_e, dirPath: string) => {
  stopWatching()
  watcher = chokidar.watch(dirPath, {
    depth: 0,
    ignoreInitial: true,
    persistent: true,
    awaitWriteFinish: { stabilityThreshold: 200, pollInterval: 50 }
  })
  const notify = (): void => {
    mainWindow?.webContents.send('fs:changed', dirPath)
  }
  watcher
    .on('add', notify)
    .on('unlink', notify)
    .on('addDir', notify)
    .on('unlinkDir', notify)
    .on('change', notify)
})

ipcMain.handle('fs:watchFile', (_e, filePath: string) => {
  stopWatchingFile()
  fileWatcher = chokidar.watch(filePath, {
    ignoreInitial: true,
    persistent: true,
    awaitWriteFinish: { stabilityThreshold: 200, pollInterval: 50 }
  })
  fileWatcher.on('change', () => {
    mainWindow?.webContents.send('fs:fileChanged', filePath)
  })
})

ipcMain.handle('fs:open', (_e, p: string) => shell.openPath(p))
ipcMain.handle('fs:reveal', (_e, p: string) => shell.showItemInFolder(p))

ipcMain.handle('clio:get-config', () => readClioConfig())

app.whenReady().then(() => {
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  stopWatching()
  stopWatchingFile()
  if (process.platform !== 'darwin') app.quit()
})
