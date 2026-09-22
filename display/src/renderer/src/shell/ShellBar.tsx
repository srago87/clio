// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import type { PaneHandle } from './types'

interface Props {
  panes: PaneHandle[]
  focusedPaneId: string
  overviewOpen: boolean
  onOpenOverview: () => void
  onFocusPane: (id: string) => void
}

export default function ShellBar({
  panes,
  focusedPaneId,
  overviewOpen,
  onOpenOverview,
  onFocusPane
}: Props): React.JSX.Element {
  return (
    <div className="flex items-center gap-1 border-b border-white/8 bg-black/30 px-2 py-1.5">
      <button
        onClick={onOpenOverview}
        title="Overview (Esc)"
        className={`flex h-8 items-center gap-2 rounded-lg px-3 text-[12px] transition-colors ${
          overviewOpen ? 'bg-white/12 text-white' : 'text-white/55 hover:bg-white/8'
        }`}
      >
        <span className="text-sm leading-none">&#9633;</span>
        Overview
      </button>

      <div className="mx-1 h-5 w-px bg-white/10" />

      {panes.map((p) => {
        const active = !overviewOpen && p.id === focusedPaneId
        return (
          <button
            key={p.id}
            onClick={() => onFocusPane(p.id)}
            className={`flex h-8 items-center gap-2 rounded-lg px-3 text-[12px] transition-colors ${
              active ? 'bg-sky-500/20 text-sky-200' : 'text-white/55 hover:bg-white/8'
            }`}
          >
            <span className="text-sm leading-none">{p.icon}</span>
            {p.title}
          </button>
        )
      })}
    </div>
  )
}
