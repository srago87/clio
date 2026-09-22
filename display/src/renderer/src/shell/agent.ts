// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import type { PaneHandle } from './types'

/* ------------------------------------------------------------------ *
 *  The seam between the shell and whatever drives it — Clio, over a
 *  local WebSocket (see useClioLink.ts).
 * ------------------------------------------------------------------ */

export interface EnvSnapshot {
  /** What the user is actually looking at right now: "overview" (the
   *  grid of all panes) or one pane's id. This is deliberately a single fact
   *  rather than two ("is the overview open" + "which pane is focused") that
   *  Clio would have to combine correctly itself — collapsing it here means
   *  there's nothing to misread. */
  visible: string
  panes: { id: string; title: string; summary: string }[]
}

export interface EnvAction {
  /** namespaced: "<paneId>.<action>" */
  name: string
  describe: string
  params?: string[]
}

export interface AgentDriver {
  /** current environment state, for the agent's context */
  getState(): EnvSnapshot
  /** everything invocable right now, across all panes */
  listActions(): EnvAction[]
  /** invoke one precisely, by name (what Clio will use) */
  invokeAction(name: string, args?: Record<string, unknown>): Promise<void> | void
  /** notified whenever env state changes */
  subscribe(onChange: () => void): () => void
}

interface LiveShell {
  handles: () => PaneHandle[]
  focusedPaneId: () => string
  overviewOpen: () => boolean
  focusPane: (id: string) => void
  openOverview: () => void
  onChange: (cb: () => void) => () => void
}

/** Build a driver backed by the shell's live pane handles. */
export function createLocalDriver(shell: LiveShell): AgentDriver {
  return {
    getState() {
      return {
        visible: shell.overviewOpen() ? 'overview' : shell.focusedPaneId(),
        panes: shell.handles().map((h) => ({ id: h.id, title: h.title, summary: h.summary }))
      }
    },
    listActions() {
      const shellActions: EnvAction[] = [
        {
          name: 'shell.openOverview',
          describe: 'show the overview (grid of all panes), leaving whatever pane is focused'
        },
        {
          name: 'shell.focusPane',
          describe: "bring one pane into view by id, replacing the overview if it was showing",
          params: ['paneId']
        }
      ]
      const paneActions = shell.handles().flatMap((h) =>
        h.actions.map((a) => ({
          name: `${h.id}.${a.name}`,
          describe: a.describe,
          params: a.params
        }))
      )
      return [...shellActions, ...paneActions]
    },
    invokeAction(name, args = {}) {
      if (name === 'shell.openOverview') {
        shell.openOverview()
        return
      }
      if (name === 'shell.focusPane') {
        const paneId = String(args.paneId ?? '')
        if (!shell.handles().some((h) => h.id === paneId)) {
          throw new Error(`no such pane: ${paneId}`)
        }
        shell.focusPane(paneId)
        return
      }
      const [paneId, action] = name.split('.')
      const pane = shell.handles().find((h) => h.id === paneId)
      const target = pane?.actions.find((a) => a.name === action)
      if (!target) throw new Error(`no such action: ${name}`)
      // Acting on a pane implies looking at it — otherwise the effect
      // happens behind the overview (or another pane) and Clio reports
      // success on a change the user never sees.
      if (shell.overviewOpen() || shell.focusedPaneId() !== paneId) {
        shell.focusPane(paneId)
      }
      return target.run(args)
    },
    subscribe(onChange) {
      return shell.onChange(onChange)
    }
  }
}
