// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import type { DirListing, FsItem } from './fs-types'

export type ViewMode = 'grid' | 'list'

export interface State {
  home: string
  cwd: string
  parent: string | null
  items: FsItem[]
  back: string[]
  forward: string[]
  selectedPath: string | null
  query: string
  view: ViewMode
  showHidden: boolean
  /** path that changed most recently on disk, for a brief highlight */
  flashPath: string | null
}

export const initialState: State = {
  home: '',
  cwd: '',
  parent: null,
  items: [],
  back: [],
  forward: [],
  selectedPath: null,
  query: '',
  view: 'grid',
  showHidden: false,
  flashPath: null
}

export type Action =
  | { type: 'init'; home: string; listing: DirListing }
  | { type: 'listing'; listing: DirListing }
  | { type: 'navigate'; to: string }
  | { type: 'back' }
  | { type: 'forward' }
  | { type: 'select'; path: string | null }
  | { type: 'setQuery'; value: string }
  | { type: 'setView'; value: ViewMode }
  | { type: 'toggleHidden' }
  | { type: 'externalChange'; listing: DirListing }
  | { type: 'clearFlash' }

export function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'init':
      return {
        ...state,
        home: action.home,
        cwd: action.listing.path,
        parent: action.listing.parent,
        items: action.listing.items
      }

    case 'listing':
      if (action.listing.path !== state.cwd) return state
      return { ...state, parent: action.listing.parent, items: action.listing.items }

    case 'navigate': {
      if (action.to === state.cwd) return state
      return {
        ...state,
        cwd: action.to,
        back: state.cwd ? [...state.back, state.cwd] : state.back,
        forward: [],
        query: '',
        selectedPath: null
      }
    }

    case 'back': {
      if (!state.back.length) return state
      const to = state.back[state.back.length - 1]
      return {
        ...state,
        cwd: to,
        back: state.back.slice(0, -1),
        forward: [state.cwd, ...state.forward],
        query: '',
        selectedPath: null
      }
    }

    case 'forward': {
      if (!state.forward.length) return state
      const [to, ...rest] = state.forward
      return {
        ...state,
        cwd: to,
        back: [...state.back, state.cwd],
        forward: rest,
        query: '',
        selectedPath: null
      }
    }

    case 'select':
      return { ...state, selectedPath: action.path }

    case 'setQuery':
      return { ...state, query: action.value }

    case 'setView':
      return { ...state, view: action.value }

    case 'toggleHidden':
      return { ...state, showHidden: !state.showHidden }

    case 'externalChange': {
      if (action.listing.path !== state.cwd) return state
      const before = new Set(state.items.map((i) => i.path))
      const added = action.listing.items.find((i) => !before.has(i.path))
      const stillThere = action.listing.items.some((i) => i.path === state.selectedPath)
      return {
        ...state,
        parent: action.listing.parent,
        items: action.listing.items,
        flashPath: added ? added.path : state.flashPath,
        selectedPath: stillThere ? state.selectedPath : null
      }
    }

    case 'clearFlash':
      return { ...state, flashPath: null }

    default:
      return state
  }
}

/** Items actually shown, in display order — say-able tags 1..N map to this. */
export function visibleItems(state: State): FsItem[] {
  const q = state.query.trim().toLowerCase()
  return state.items
    .filter((i) => state.showHidden || !i.hidden)
    .filter((i) => !q || i.name.toLowerCase().includes(q))
    .sort((a, b) => {
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1
      return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
    })
}
