// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import { useCallback, useEffect, useMemo, useReducer, useState } from 'react'
import DocumentBody from './DocumentBody'
import { parseBlocks, matchBlocks } from './blocks'
import { initialState, reducer } from './store'
import { useShell } from '../../shell/ShellContext'
import type { PaneAction, PaneHandle, PaneModule } from '../../shell/types'

const ID = 'document'

function shortPath(full: string, home: string): string {
  if (home && (full === home || full.startsWith(home + '/'))) return '~' + full.slice(home.length)
  return full
}

function useDocument(): PaneHandle {
  const shell = useShell()
  const [state, dispatch] = useReducer(reducer, initialState)
  const [home, setHome] = useState('')

  useEffect(() => {
    void window.fs.home().then(setHome)
  }, [])

  const load = useCallback((path: string) => {
    dispatch({ type: 'load', path })
  }, [])

  // read + watch whenever the path changes
  useEffect(() => {
    if (!state.path) return
    let cancelled = false
    void window.fs
      .readFile(state.path)
      .then((content) => {
        if (!cancelled) dispatch({ type: 'loaded', content, blocks: parseBlocks(content) })
      })
      .catch((e: unknown) => {
        if (!cancelled) dispatch({ type: 'loadError', message: e instanceof Error ? e.message : String(e) })
      })
    void window.fs.watchFile(state.path)
    return () => {
      cancelled = true
    }
  }, [state.path])

  // live updates from disk
  useEffect(() => {
    return window.fs.onFileChanged((path) => {
      if (path !== state.path) return
      void window.fs.readFile(path).then((content) => {
        dispatch({ type: 'externalChange', content, blocks: parseBlocks(content) })
        shell.pushLog('Document changed on disk', 'change', ID)
      })
    })
  }, [state.path, shell])

  // env changed -> nudge the driver's subscribers
  useEffect(() => {
    shell.notifyChange()
  }, [state, shell])

  const selectBlock = useCallback((id: string | null) => {
    dispatch({ type: 'select', id })
  }, [])

  const actions: PaneAction[] = useMemo(
    () => [
      {
        name: 'open',
        describe: 'open a markdown file by path',
        params: ['path'],
        run: (args) => load(String(args.path ?? ''))
      },
      {
        name: 'select',
        describe: 'select a block by tag number or matching text',
        params: ['target'],
        run: (args) => {
          const hits = matchBlocks(String(args.target ?? ''), state.blocks)
          if (hits.length === 1) selectBlock(hits[0].id)
        }
      }
    ],
    [load, selectBlock, state.blocks]
  )

  const summary = state.path
    ? `${shortPath(state.path, home)} · ${state.blocks.length} block${state.blocks.length === 1 ? '' : 's'}`
    : 'no document open'

  const body = <DocumentBody state={state} onSelect={selectBlock} />

  return { id: ID, title: 'Document', icon: '\u{1F4C4}', body, summary, actions }
}

export const documentPane: PaneModule = {
  id: ID,
  title: 'Document',
  icon: '\u{1F4C4}',
  use: useDocument
}
