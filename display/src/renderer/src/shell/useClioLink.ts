// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import { useEffect } from 'react'
import type { AgentDriver } from './agent'

/* ------------------------------------------------------------------ *
 *  Opens the WebSocket link to Clio's local-only listener and answers
 *  whatever it asks — getState / listActions / invokeAction — using the
 *  driver already built from live shell state. Reconnects with backoff
 *  since Clio may not be up yet, or may restart while this is open.
 * ------------------------------------------------------------------ */

interface ClioRequest {
  id: string
  method: 'getState' | 'listActions' | 'invokeAction'
  args?: { name?: string; args?: Record<string, unknown> }
}

const RETRY_MS = 3000

export function useClioLink(driver: AgentDriver): void {
  useEffect(() => {
    let ws: WebSocket | null = null
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    let cancelled = false

    async function respond(req: ClioRequest): Promise<void> {
      try {
        let result: unknown
        if (req.method === 'getState') result = driver.getState()
        else if (req.method === 'listActions') result = driver.listActions()
        else if (req.method === 'invokeAction') {
          await driver.invokeAction(req.args?.name ?? '', req.args?.args ?? {})
          result = { ok: true }
        } else {
          throw new Error(`unknown method: ${req.method}`)
        }
        ws?.send(JSON.stringify({ id: req.id, result }))
      } catch (e) {
        ws?.send(JSON.stringify({ id: req.id, error: e instanceof Error ? e.message : String(e) }))
      }
    }

    async function connect(): Promise<void> {
      if (cancelled) return
      const config = await window.clio.getConfig()
      if (cancelled) return
      if (!config) {
        retryTimer = setTimeout(connect, RETRY_MS)
        return
      }

      const url = `${config.wsUrl}?token=${encodeURIComponent(config.token)}`
      ws = new WebSocket(url)

      ws.onmessage = (event: MessageEvent<string>) => {
        let req: ClioRequest
        try {
          req = JSON.parse(event.data)
        } catch {
          return
        }
        void respond(req)
      }

      ws.onclose = () => {
        if (!cancelled) retryTimer = setTimeout(connect, RETRY_MS)
      }
      ws.onerror = () => {
        ws?.close()
      }
    }

    connect()

    return () => {
      cancelled = true
      if (retryTimer) clearTimeout(retryTimer)
      ws?.close()
    }
  }, [driver])
}
