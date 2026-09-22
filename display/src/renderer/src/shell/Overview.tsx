// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import type { PaneHandle } from './types'

interface Props {
  panes: PaneHandle[]
  onFocus: (id: string) => void
}

/* The home screen. Shown when no pane is focused — an overview of every
 * pane and its current state, and the way you pick one. */
export default function Overview({ panes, onFocus }: Props): React.JSX.Element {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-8 p-10">
      <div className="text-center">
        <div className="text-[13px] font-semibold uppercase tracking-[0.2em] text-white/30">Clio's Display</div>
        <div className="mt-1 text-[13px] text-white/40">Say where you want to go, or pick a pane.</div>
      </div>

      <div className="grid w-full max-w-3xl grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3">
        {panes.map((p) => (
          <button
            key={p.id}
            onClick={() => onFocus(p.id)}
            className="flex flex-col gap-2 rounded-2xl border border-white/8 bg-white/[0.03] p-4 text-left transition-colors hover:border-white/16 hover:bg-white/[0.06]"
          >
            <span className="flex items-center gap-2.5">
              <span className="text-2xl leading-none">{p.icon}</span>
              <span className="text-[14px] font-medium text-white/85">{p.title}</span>
            </span>
            <span className="line-clamp-2 text-[12px] leading-snug text-white/45">{p.summary}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
