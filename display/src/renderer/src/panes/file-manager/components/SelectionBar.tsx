// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import { iconFor, formatSize, formatDate, shortPath } from '../format'
import type { FsItem } from '../fs-types'

interface Props {
  selected: FsItem | null
  home: string
  count: number
  onOpen: (item: FsItem) => void
  onReveal: (item: FsItem) => void
}

export default function SelectionBar({ selected, home, count, onOpen, onReveal }: Props): React.JSX.Element {
  if (!selected) {
    return (
      <div className="flex items-center gap-2 border-b border-white/8 bg-black/10 px-4 py-2 text-[13px] text-white/40">
        <span className="inline-block h-2 w-2 rounded-full bg-white/20" />
        Nothing selected
        <span className="ml-auto text-white/30">{count} items</span>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-3 border-b border-white/8 bg-sky-500/10 px-4 py-2">
      <span className="text-xl leading-none">{iconFor(selected)}</span>
      <div className="min-w-0">
        <div className="truncate text-[13px] font-semibold text-sky-100">
          Selected: {selected.name}
        </div>
        <div className="truncate text-[11px] text-white/45">
          {shortPath(selected.path, home)}
          {' · '}
          {selected.isDir ? 'folder' : formatSize(selected.size, false)}
          {' · '}
          {formatDate(selected.mtime)}
        </div>
      </div>
      <div className="ml-auto flex shrink-0 gap-1.5">
        <button
          onClick={() => onOpen(selected)}
          className="rounded-lg bg-white/10 px-3 py-1.5 text-[12px] text-white/80 hover:bg-white/16"
        >
          {selected.isDir ? 'Open' : 'Open externally'}
        </button>
        <button
          onClick={() => onReveal(selected)}
          className="rounded-lg bg-white/10 px-3 py-1.5 text-[12px] text-white/80 hover:bg-white/16"
        >
          Reveal
        </button>
      </div>
    </div>
  )
}
