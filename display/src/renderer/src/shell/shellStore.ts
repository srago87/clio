// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import type { LogEntry, LogKind } from './types'

export interface ShellState {
  focusedPaneId: string
  overviewOpen: boolean
  log: LogEntry[]
}

export function initialShellState(firstPaneId: string): ShellState {
  return {
    focusedPaneId: firstPaneId,
    overviewOpen: false,
    log: []
  }
}

let logSeq = 0
function append(log: LogEntry[], text: string, kind: LogKind, paneId?: string): LogEntry[] {
  return [...log.slice(-119), { id: ++logSeq, ts: Date.now(), text, kind, paneId }]
}

export type ShellAction =
  | { type: 'focusPane'; id: string }
  | { type: 'openOverview' }
  | { type: 'closeOverview' }
  | { type: 'toggleOverview' }
  | { type: 'log'; text: string; kind: LogKind; paneId?: string }

export function shellReducer(state: ShellState, action: ShellAction): ShellState {
  switch (action.type) {
    case 'focusPane':
      return { ...state, focusedPaneId: action.id, overviewOpen: false }
    case 'openOverview':
      return { ...state, overviewOpen: true }
    case 'closeOverview':
      return { ...state, overviewOpen: false }
    case 'toggleOverview':
      return { ...state, overviewOpen: !state.overviewOpen }
    case 'log':
      return { ...state, log: append(state.log, action.text, action.kind, action.paneId) }
    default:
      return state
  }
}
