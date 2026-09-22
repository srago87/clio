// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import type { ReactNode } from 'react'

/* ------------------------------------------------------------------ *
 *  Shell <-> pane contract
 *
 *  The shell owns: which pane is focused, the running history, and the
 *  seam to whatever is driving it (Clio).
 *
 *  A pane owns: its own state and chrome, and the list of actions Clio
 *  may invoke on it by name.
 * ------------------------------------------------------------------ */

export type LogKind = 'nav' | 'select' | 'change' | 'info' | 'action'

export interface LogEntry {
  id: number
  ts: number
  text: string
  kind: LogKind
  paneId?: string
}

/** A callable a pane exposes, invoked by Clio through the shell's driver. */
export interface PaneAction {
  name: string
  describe: string
  /** rough parameter names, until there's a real schema */
  params?: string[]
  run: (args: Record<string, unknown>) => void | Promise<void>
}

/** Live view of a pane, produced fresh by its module's `use()` hook on
 *  every render so `summary` always sees current state. */
export interface PaneHandle {
  id: string
  title: string
  icon: string
  /** shown when this pane is focused */
  body: ReactNode
  /** one line for the overview */
  summary: string
  /** everything a driver may invoke on this pane right now */
  actions: PaneAction[]
}

/** Static registration. `use()` is a hook the shell calls unconditionally,
 *  so panes may hold state, run effects, and read shell context. */
export interface PaneModule {
  id: string
  title: string
  icon: string
  use: () => PaneHandle
}
