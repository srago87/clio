// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import { crumbs } from '../format'
import type { ViewMode } from '../store'

interface Props {
  home: string
  cwd: string
  canBack: boolean
  canForward: boolean
  canUp: boolean
  query: string
  view: ViewMode
  showHidden: boolean
  onBack: () => void
  onForward: () => void
  onUp: () => void
  onCrumb: (path: string, label: string) => void
  onQuery: (value: string) => void
  onView: (v: ViewMode) => void
  onToggleHidden: () => void
}

function IconButton({
  label,
  disabled,
  onClick,
  children
}: {
  label: string
  disabled?: boolean
  onClick: () => void
  children: React.ReactNode
}): React.JSX.Element {
  return (
    <button
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="flex h-8 w-8 items-center justify-center rounded-lg text-white/70 transition-colors hover:bg-white/8 disabled:opacity-25 disabled:hover:bg-transparent"
    >
      {children}
    </button>
  )
}

export default function Toolbar({
  home,
  cwd,
  canBack,
  canForward,
  canUp,
  query,
  view,
  showHidden,
  onBack,
  onForward,
  onUp,
  onCrumb,
  onQuery,
  onView,
  onToggleHidden
}: Props): React.JSX.Element {
  const parts = crumbs(cwd, home)

  return (
    <div className="flex items-center gap-2 border-b border-white/8 bg-black/15 px-3 py-2">
      <div className="flex items-center gap-0.5">
        <IconButton label="Back" disabled={!canBack} onClick={onBack}>
          <span className="text-lg leading-none">&#8592;</span>
        </IconButton>
        <IconButton label="Forward" disabled={!canForward} onClick={onForward}>
          <span className="text-lg leading-none">&#8594;</span>
        </IconButton>
        <IconButton label="Up one level" disabled={!canUp} onClick={onUp}>
          <span className="text-lg leading-none">&#8593;</span>
        </IconButton>
      </div>

      <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto rounded-lg bg-white/5 px-2.5 py-1.5 text-[13px]">
        {parts.map((c, i) => (
          <span key={c.path} className="flex shrink-0 items-center gap-1">
            {i > 0 && <span className="text-white/25">/</span>}
            <button
              onClick={() => onCrumb(c.path, c.label)}
              className={`rounded px-1 py-0.5 hover:bg-white/10 ${
                i === parts.length - 1 ? 'font-medium text-white' : 'text-white/55'
              }`}
            >
              {c.label}
            </button>
          </span>
        ))}
      </div>

      <label className="flex items-center gap-2 rounded-lg bg-white/5 px-2.5 py-1.5">
        <span className="text-white/35">&#128269;</span>
        <input
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          placeholder="Filter this folder"
          className="w-44 bg-transparent text-[13px] text-white placeholder:text-white/30 focus:outline-none"
        />
        {query && (
          <button onClick={() => onQuery('')} className="text-white/40 hover:text-white/80" aria-label="Clear filter">
            &#10005;
          </button>
        )}
      </label>

      <button
        onClick={onToggleHidden}
        title="Show hidden files"
        className={`h-8 rounded-lg px-2.5 text-[12px] transition-colors ${
          showHidden ? 'bg-sky-500/20 text-sky-200' : 'text-white/50 hover:bg-white/8'
        }`}
      >
        .hidden
      </button>

      <div className="flex overflow-hidden rounded-lg bg-white/5">
        <button
          onClick={() => onView('grid')}
          title="Grid view"
          className={`h-8 w-8 text-sm ${view === 'grid' ? 'bg-white/12 text-white' : 'text-white/45'}`}
        >
          &#9638;
        </button>
        <button
          onClick={() => onView('list')}
          title="List view"
          className={`h-8 w-8 text-sm ${view === 'list' ? 'bg-white/12 text-white' : 'text-white/45'}`}
        >
          &#9776;
        </button>
      </div>
    </div>
  )
}
