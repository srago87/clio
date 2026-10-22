// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import { contextBridge, ipcRenderer } from 'electron'

const api = {
  home: (): Promise<string> => ipcRenderer.invoke('fs:home'),
  readFile: (p: string): Promise<string> => ipcRenderer.invoke('fs:readFile', p),
  watchFile: (p: string): Promise<void> => ipcRenderer.invoke('fs:watchFile', p),
  onFileChanged: (cb: (path: string) => void): (() => void) => {
    const listener = (_e: unknown, path: string): void => cb(path)
    ipcRenderer.on('fs:fileChanged', listener)
    return () => {
      ipcRenderer.removeListener('fs:fileChanged', listener)
    }
  }
}

contextBridge.exposeInMainWorld('fs', api)

export type FsApi = typeof api

const clioApi = {
  getConfig: (): Promise<{ wsUrl: string; token: string } | null> =>
    ipcRenderer.invoke('clio:get-config')
}

contextBridge.exposeInMainWorld('clio', clioApi)

export type ClioApi = typeof clioApi
