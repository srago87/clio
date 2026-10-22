// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import { createContext, useContext } from 'react'
import type { LogKind } from './types'

/** The slice of the shell a pane module may touch from inside its `use()` hook. */
export interface ShellApi {
  focusedPaneId: string
  focusPane: (id: string) => void
  /** add a line to the running history */
  pushLog: (text: string, kind: LogKind, paneId?: string) => void
  /** bump the env-changed subscribers (call after a pane mutates state) */
  notifyChange: () => void
}

export const ShellContext = createContext<ShellApi | null>(null)

export function useShell(): ShellApi {
  const ctx = useContext(ShellContext)
  if (!ctx) throw new Error('useShell must be used within <Shell>')
  return ctx
}
