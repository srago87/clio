// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import { useEffect, useRef } from 'react'
import type { Terminal } from '@xterm/xterm'
import type { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

interface Props {
  term: Terminal | null
  fit: FitAddon | null
  owner: 'human' | 'clio'
  onResize: (cols: number, rows: number) => void
}

export default function TerminalBody({ term, fit, owner, onResize }: Props): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)

  // Re-attach the (persistent) Terminal instance into this pane's freshly
  // mounted container every time it becomes focused — xterm.js supports
  // open() being called again when its element changes, so history and the
  // live connection survive switching away from this pane and back.
  useEffect(() => {
    if (!term || !fit || !containerRef.current) return
    term.open(containerRef.current)
    fit.fit()
    onResize(term.cols, term.rows)

    const observer = new ResizeObserver(() => {
      fit.fit()
      onResize(term.cols, term.rows)
    })
    observer.observe(containerRef.current)
    return () => observer.disconnect()
  }, [term, fit, onResize])

  return (
    <div className="flex h-full flex-col bg-[#0f1115]">
      {owner === 'clio' && (
        <div className="border-b border-amber-400/20 bg-amber-400/10 px-3 py-1.5 text-[12px] text-amber-200/90">
          Clio is working — press Esc to take over
        </div>
      )}
      <div className="min-h-0 flex-1 p-2">
        <div ref={containerRef} className="h-full w-full" />
      </div>
    </div>
  )
}
