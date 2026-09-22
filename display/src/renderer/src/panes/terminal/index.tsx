// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import { useCallback, useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import TerminalBody from './TerminalBody'
import type { PaneHandle, PaneModule } from '../../shell/types'

const ID = 'terminal'
const RETRY_MS = 3000

type Owner = 'human' | 'clio'

/** A real, shared, turn-based terminal: Clio (bash_command) and the human
 *  drive the exact same server-side shell session, one at a time. The
 *  Terminal instance and WebSocket live here (in the pane's `use()` hook,
 *  called every render regardless of focus) rather than in the body
 *  component, so history and connection survive switching away from this
 *  pane and back — xterm.js explicitly supports re-`open()`-ing into a new
 *  container when its element changes, which is what TerminalBody does on
 *  each (re)mount. */
function useTerminalPane(): PaneHandle {
  const [owner, setOwner] = useState<Owner>('human')
  const ownerRef = useRef<Owner>('human')
  ownerRef.current = owner

  const termRef = useRef<Terminal | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const wsRef = useRef<WebSocket | null>(null)

  if (!termRef.current) {
    const term = new Terminal({
      convertEol: true,
      fontSize: 13,
      fontFamily: 'ui-monospace, "Cascadia Code", Menlo, Consolas, monospace',
      theme: { background: '#0f1115', foreground: '#e2e2e2' },
      scrollback: 5000
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    termRef.current = term
    fitRef.current = fit
  }

  // WebSocket to the shared terminal session — separate from useClioLink's
  // /ws/display (that's the getState/listActions/invokeAction protocol; this
  // is raw terminal I/O, a different kind of connection entirely).
  useEffect(() => {
    let cancelled = false
    let retryTimer: ReturnType<typeof setTimeout> | null = null

    async function connect(): Promise<void> {
      if (cancelled) return
      const config = await window.clio.getConfig()
      if (cancelled) return
      if (!config) {
        retryTimer = setTimeout(connect, RETRY_MS)
        return
      }
      const terminalWsUrl = config.wsUrl.replace('/ws/display', '/ws/terminal')
      const ws = new WebSocket(`${terminalWsUrl}?token=${encodeURIComponent(config.token)}`)
      wsRef.current = ws

      ws.onmessage = (event: MessageEvent<string>) => {
        let msg: { type?: string; data?: string; owner?: string }
        try {
          msg = JSON.parse(event.data)
        } catch {
          return
        }
        if (msg.type === 'output' && typeof msg.data === 'string') {
          termRef.current?.write(msg.data)
        } else if (msg.type === 'owner' && (msg.owner === 'human' || msg.owner === 'clio')) {
          setOwner(msg.owner)
        }
      }
      ws.onclose = () => {
        if (!cancelled) retryTimer = setTimeout(connect, RETRY_MS)
      }
      ws.onerror = () => ws.close()
    }

    void connect()
    return () => {
      cancelled = true
      if (retryTimer) clearTimeout(retryTimer)
      wsRef.current?.close()
    }
  }, [])

  // Keystrokes: a bare Esc always requests taking control back (interrupting
  // whatever Clio's running); anything else is only forwarded while it's
  // actually the human's turn — otherwise it's just dropped, which is what
  // "locked out" should feel like (no local echo of keys that go nowhere).
  useEffect(() => {
    const term = termRef.current
    if (!term) return
    const subscription = term.onData((data) => {
      if (data === '\x1b') {
        wsRef.current?.send(JSON.stringify({ type: 'interrupt' }))
        return
      }
      if (ownerRef.current === 'human') {
        wsRef.current?.send(JSON.stringify({ type: 'input', data }))
      }
    })
    return () => subscription.dispose()
  }, [])

  const sendResize = useCallback((cols: number, rows: number) => {
    wsRef.current?.send(JSON.stringify({ type: 'resize', cols, rows }))
  }, [])

  const body = <TerminalBody term={termRef.current} fit={fitRef.current} owner={owner} onResize={sendResize} />

  const summary = owner === 'clio' ? 'Clio has control' : 'ready'

  // No actions — Clio drives this through bash_command directly against the
  // shared session, not through display_invoke_action.
  return { id: ID, title: 'Terminal', icon: '\u{1F5A5}\u{FE0F}', body, summary, actions: [] }
}

export const terminalPane: PaneModule = {
  id: ID,
  title: 'Terminal',
  icon: '\u{1F5A5}\u{FE0F}',
  use: useTerminalPane
}
