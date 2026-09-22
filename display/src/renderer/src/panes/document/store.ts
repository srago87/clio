// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import type { Block } from './blocks'

export interface State {
  path: string | null
  content: string
  blocks: Block[]
  selectedBlockId: string | null
  loading: boolean
  error: string | null
}

export const initialState: State = {
  path: null,
  content: '',
  blocks: [],
  selectedBlockId: null,
  loading: false,
  error: null
}

export type Action =
  | { type: 'load'; path: string }
  | { type: 'loaded'; content: string; blocks: Block[] }
  | { type: 'loadError'; message: string }
  | { type: 'externalChange'; content: string; blocks: Block[] }
  | { type: 'select'; id: string | null }

export function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'load':
      if (action.path === state.path) return state
      return { ...state, path: action.path, loading: true, error: null, selectedBlockId: null }

    case 'loaded':
      return { ...state, content: action.content, blocks: action.blocks, loading: false, error: null }

    case 'loadError':
      return { ...state, loading: false, error: action.message }

    case 'externalChange': {
      const stillThere = action.blocks.some((b) => b.id === state.selectedBlockId)
      return {
        ...state,
        content: action.content,
        blocks: action.blocks,
        selectedBlockId: stillThere ? state.selectedBlockId : null
      }
    }

    case 'select':
      return { ...state, selectedBlockId: action.id }

    default:
      return state
  }
}
