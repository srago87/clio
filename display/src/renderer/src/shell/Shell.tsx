// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import { useEffect, useCallback, useMemo, useReducer, useRef } from 'react'
import ShellBar from './ShellBar'
import Overview from './Overview'
import AmbientRail from './AmbientRail'
import { ShellContext, type ShellApi } from './ShellContext'
import { initialShellState, shellReducer, type ShellAction, type ShellState } from './shellStore'
import type { PaneHandle } from './types'
import { createLocalDriver } from './agent'
import { useClioLink } from './useClioLink'
import { fileManagerPane } from '../panes/file-manager'
import { documentPane } from '../panes/document'
import { terminalPane } from '../panes/terminal'

/* Pane registry. Adding a pane = import its module and list it here;
 * ShellInner calls each module's use() hook in this fixed order. */

export default function Shell(): React.JSX.Element {
  const [state, dispatch] = useReducer(shellReducer, fileManagerPane.id, initialShellState)
  const subscribers = useRef<Set<() => void>>(new Set())

  const pushLog = useCallback<ShellApi['pushLog']>((text, kind, paneId) => {
    dispatch({ type: 'log', text, kind, paneId })
  }, [])
  const focusPane = useCallback<ShellApi['focusPane']>((id) => {
    dispatch({ type: 'focusPane', id })
  }, [])
  const notifyChange = useCallback<ShellApi['notifyChange']>(() => {
    subscribers.current.forEach((cb) => cb())
  }, [])
  const requestOpenDocument = useCallback<ShellApi['requestOpenDocument']>((path) => {
    dispatch({ type: 'requestOpenDocument', path })
  }, [])
  const clearOpenDocumentRequest = useCallback<ShellApi['clearOpenDocumentRequest']>(() => {
    dispatch({ type: 'clearOpenDocumentRequest' })
  }, [])

  const api = useMemo<ShellApi>(
    () => ({
      focusedPaneId: state.focusedPaneId,
      focusPane,
      pushLog,
      notifyChange,
      openDocumentRequest: state.openDocumentRequest,
      requestOpenDocument,
      clearOpenDocumentRequest
    }),
    [state.focusedPaneId, state.openDocumentRequest, focusPane, pushLog, notifyChange, requestOpenDocument, clearOpenDocumentRequest]
  )

  return (
    <ShellContext.Provider value={api}>
      <ShellInner state={state} dispatch={dispatch} />
    </ShellContext.Provider>
  )
}

function ShellInner({
  state,
  dispatch
}: {
  state: ShellState
  dispatch: React.Dispatch<ShellAction>
}): React.JSX.Element {
  // Pane hooks — fixed order, one line per registered pane.
  const fm = fileManagerPane.use()
  const doc = documentPane.use()
  const term = terminalPane.use()
  const handles: PaneHandle[] = [fm, doc, term]

  const handlesRef = useRef(handles)
  handlesRef.current = handles

  const stateRef = useRef(state)
  stateRef.current = state

  const focused = handles.find((h) => h.id === state.focusedPaneId) ?? handles[0]

  // Clio reaches this GUI through this driver — see shell/agent.ts. Built
  // once via a ref so useClioLink's WebSocket connection doesn't reconnect
  // on every render; each method still reads current pane/focus state
  // through the refs above.
  const driverRef = useRef<ReturnType<typeof createLocalDriver> | null>(null)
  if (!driverRef.current) {
    driverRef.current = createLocalDriver({
      handles: () => handlesRef.current,
      focusedPaneId: () => stateRef.current.focusedPaneId,
      overviewOpen: () => stateRef.current.overviewOpen,
      focusPane: (id) => dispatch({ type: 'focusPane', id }),
      openOverview: () => dispatch({ type: 'openOverview' }),
      onChange: () => () => {}
    })
  }
  useClioLink(driverRef.current)

  // Esc -> overview (except in the terminal pane, where Esc means "interrupt
  // Clio and take back control" — see panes/terminal/index.tsx. Global Esc
  // handling would otherwise yank focus to the overview instead, on top of
  // xterm.js's own hidden textarea already stealing the blur-only case).
  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      if (e.key !== 'Escape') return
      if (state.focusedPaneId === 'terminal' && !state.overviewOpen) return
      const el = e.target as HTMLElement
      if (el?.tagName === 'INPUT' || el?.tagName === 'TEXTAREA') el.blur()
      dispatch({ type: 'openOverview' })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dispatch, state.focusedPaneId, state.overviewOpen])

  return (
    <div className="flex h-full flex-col bg-[#0f1115] text-white">
      <ShellBar
        panes={handles}
        focusedPaneId={state.focusedPaneId}
        overviewOpen={state.overviewOpen}
        onOpenOverview={() => dispatch({ type: 'openOverview' })}
        onFocusPane={(id) => dispatch({ type: 'focusPane', id })}
      />
      <div className="flex min-h-0 flex-1">
        <div className="relative flex min-w-0 flex-1 flex-col">
          {state.overviewOpen ? (
            <Overview panes={handles} onFocus={(id) => dispatch({ type: 'focusPane', id })} />
          ) : (
            <div className="min-h-0 flex-1">{focused.body}</div>
          )}
        </div>
        <AmbientRail log={state.log} />
      </div>
    </div>
  )
}
