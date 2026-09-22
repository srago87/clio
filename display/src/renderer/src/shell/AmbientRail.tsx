// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import { useEffect, useRef } from 'react'
import type { LogEntry, LogKind } from './types'

/* The always-on status column. Today it holds one section — History.
 * Running work (Clio's in-flight operations) becomes a second section here. */

const DOT: Record<LogKind, string> = {
  nav: 'bg-sky-400',
  select: 'bg-violet-400',
  change: 'bg-amber-400',
  info: 'bg-white/40',
  action: 'bg-emerald-400'
}

function timeLabel(ts: number): string {
  return new Date(ts).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export default function AmbientRail({ log }: { log: LogEntry[] }): React.JSX.Element {
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [log.length])

  return (
    <aside className="flex w-64 shrink-0 flex-col border-l border-white/8 bg-black/20">
      <div className="border-b border-white/8 px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-white/35">
        History
      </div>
      <div className="flex-1 overflow-auto px-3 py-2">
        {log.length === 0 ? (
          <p className="pt-4 text-center text-[12px] text-white/25">What you do and what changes shows up here.</p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {log.map((e) => (
              <li key={e.id} className="flex gap-2 text-[12px] leading-snug">
                <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${DOT[e.kind]}`} />
                <span className="min-w-0">
                  <span className="text-white/75">{e.text}</span>
                  <span className="ml-1.5 text-[10px] text-white/25">{timeLabel(e.ts)}</span>
                </span>
              </li>
            ))}
            <div ref={endRef} />
          </ul>
        )}
      </div>
    </aside>
  )
}
