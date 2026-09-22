// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import { iconFor, formatSize, formatDate } from '../format'
import type { FsItem } from '../fs-types'
import type { ViewMode } from '../store'

interface Props {
  items: FsItem[]
  view: ViewMode
  selectedPath: string | null
  flashPath: string | null
  query: string
  onSelect: (item: FsItem) => void
  onOpen: (item: FsItem) => void
}

/** Say-able tag shown on the corner of every item: 1..N in display order. */
function Tag({ n, active }: { n: number; active: boolean }): React.JSX.Element {
  return (
    <span
      className={`pointer-events-none absolute left-1.5 top-1.5 min-w-[18px] rounded-md px-1 text-center text-[11px] font-semibold tabular-nums ${
        active ? 'bg-sky-400 text-slate-900' : 'bg-slate-900/80 text-white/80 ring-1 ring-white/15'
      }`}
    >
      {n}
    </span>
  )
}

export default function FileView({
  items,
  view,
  selectedPath,
  flashPath,
  query,
  onSelect,
  onOpen
}: Props): React.JSX.Element {
  if (items.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-white/35">
        {query ? `Nothing matches "${query}" in this folder` : 'This folder is empty'}
      </div>
    )
  }

  if (view === 'list') {
    return (
      <div className="flex-1 overflow-auto">
        <table className="w-full border-collapse text-[13px]">
          <thead className="sticky top-0 bg-[#12151b] text-left text-[11px] uppercase tracking-wide text-white/35">
            <tr>
              <th className="w-14 py-2 pl-4 font-medium">Tag</th>
              <th className="py-2 font-medium">Name</th>
              <th className="w-28 py-2 font-medium">Size</th>
              <th className="w-36 py-2 pr-4 font-medium">Modified</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item, i) => {
              const active = item.path === selectedPath
              const flash = item.path === flashPath
              return (
                <tr
                  key={item.path}
                  onClick={() => onSelect(item)}
                  onDoubleClick={() => onOpen(item)}
                  className={`cursor-default border-b border-white/5 transition-colors ${
                    active ? 'bg-sky-500/20' : flash ? 'bg-amber-400/15' : 'hover:bg-white/5'
                  }`}
                >
                  <td className="py-1.5 pl-4">
                    <span
                      className={`inline-block min-w-[20px] rounded-md px-1 text-center text-[11px] font-semibold tabular-nums ${
                        active ? 'bg-sky-400 text-slate-900' : 'bg-white/10 text-white/70'
                      }`}
                    >
                      {i + 1}
                    </span>
                  </td>
                  <td className="py-1.5">
                    <span className="mr-2">{iconFor(item)}</span>
                    <span className={item.hidden ? 'text-white/45' : 'text-white/90'}>{item.name}</span>
                  </td>
                  <td className="py-1.5 text-white/50">{formatSize(item.size, item.isDir)}</td>
                  <td className="py-1.5 pr-4 text-white/50">{formatDate(item.mtime)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-auto p-4">
      <div className="grid grid-cols-[repeat(auto-fill,minmax(116px,1fr))] gap-2">
        {items.map((item, i) => {
          const active = item.path === selectedPath
          const flash = item.path === flashPath
          return (
            <button
              key={item.path}
              onClick={() => onSelect(item)}
              onDoubleClick={() => onOpen(item)}
              className={`relative flex flex-col items-center gap-1.5 rounded-xl px-2 py-3 text-center transition-colors ${
                active
                  ? 'bg-sky-500/20 ring-1 ring-sky-400/60'
                  : flash
                    ? 'bg-amber-400/15 ring-1 ring-amber-300/50'
                    : 'hover:bg-white/6'
              }`}
            >
              <Tag n={i + 1} active={active} />
              <span className="text-4xl leading-none">{iconFor(item)}</span>
              <span
                className={`line-clamp-2 break-words text-[12px] leading-tight ${
                  item.hidden ? 'text-white/40' : 'text-white/85'
                }`}
              >
                {item.name}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
