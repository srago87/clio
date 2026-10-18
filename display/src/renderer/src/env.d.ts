/// <reference types="vite/client" />

// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

declare global {
  interface Window {
    fs: {
      home: () => Promise<string>
      readFile: (p: string) => Promise<string>
      watchFile: (p: string) => Promise<void>
      onFileChanged: (cb: (path: string) => void) => () => void
    }
    clio: {
      getConfig: () => Promise<{ wsUrl: string; token: string } | null>
    }
  }
}

export {}
