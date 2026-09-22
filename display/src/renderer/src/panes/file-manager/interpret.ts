// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import type { FsItem } from './fs-types'

/** Resolve a spoken/typed name or tag reference against what's on screen,
 *  for pane actions (e.g. "open"/"select") that take a `target` argument. */
export function matchItems(needle: string, items: FsItem[]): FsItem[] {
  const n = needle.toLowerCase().replace(/^(the|a|an) /, '').trim()
  if (!n) return []
  const exact = items.filter((i) => i.name.toLowerCase() === n)
  if (exact.length) return exact
  const starts = items.filter((i) => i.name.toLowerCase().startsWith(n))
  if (starts.length) return starts
  const contains = items.filter((i) => i.name.toLowerCase().includes(n))
  if (contains.length) return contains
  // token overlap — "budget report" against "Q3-budget-report-final.pdf"
  const toks = n.split(/\s+/).filter(Boolean)
  return items.filter((i) => {
    const hay = i.name.toLowerCase()
    return toks.length > 1 && toks.every((t) => hay.includes(t))
  })
}
