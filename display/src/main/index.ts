// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import { app, BrowserWindow, ipcMain } from 'electron'
import { join } from 'path'
import { homedir } from 'os'
import { readFile } from 'fs/promises'
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

function stopWatchingFile(): void {
  fileWatcher?.close()
  fileWatcher = null
}

ipcMain.handle('fs:home', () => homedir())

ipcMain.handle('fs:readFile', (_e, filePath: string) => readFile(filePath, 'utf-8'))

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

ipcMain.handle('clio:get-config', () => readClioConfig())

app.whenReady().then(() => {
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  stopWatchingFile()
  if (process.platform !== 'darwin') app.quit()
})
