// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import { useCallback, useEffect, useMemo, useReducer, useRef } from 'react'
import FileManagerBody from './FileManagerBody'
import { shortPath } from './format'
import { matchItems } from './interpret'
import { initialState, reducer, visibleItems } from './store'
import type { FsItem } from './fs-types'
import { useShell } from '../../shell/ShellContext'
import type { PaneAction, PaneHandle, PaneModule } from '../../shell/types'

const ID = 'file-manager'

function useFileManager(): PaneHandle {
  const shell = useShell()
  const [state, dispatch] = useReducer(reducer, initialState)
  const flashTimer = useRef<ReturnType<typeof setTimeout>>(undefined)

  // initial load
  useEffect(() => {
    void (async () => {
      const home = await window.fs.home()
      const listing = await window.fs.list(home)
      dispatch({ type: 'init', home, listing })
    })()
  }, [])

  // reload + rewatch whenever the folder changes
  useEffect(() => {
    if (!state.cwd) return
    let cancelled = false
    void window.fs.list(state.cwd).then((listing) => {
      if (!cancelled) dispatch({ type: 'listing', listing })
    })
    void window.fs.watch(state.cwd)
    return () => {
      cancelled = true
    }
  }, [state.cwd])

  // live updates from disk
  useEffect(() => {
    return window.fs.onChanged((dir) => {
      void window.fs.list(dir).then((listing) => {
        dispatch({ type: 'externalChange', listing })
        if (dir === state.cwd) shell.pushLog('Folder contents changed on disk', 'change', ID)
      })
    })
  }, [state.cwd, shell])

  // clear the change highlight
  useEffect(() => {
    if (!state.flashPath) return
    clearTimeout(flashTimer.current)
    flashTimer.current = setTimeout(() => dispatch({ type: 'clearFlash' }), 2500)
  }, [state.flashPath])

  // env changed -> nudge the driver's subscribers
  useEffect(() => {
    shell.notifyChange()
  }, [state, shell])

  const visible = useMemo(() => visibleItems(state), [state])
  const selected = useMemo(
    () => state.items.find((i) => i.path === state.selectedPath) ?? null,
    [state.items, state.selectedPath]
  )

  const go = useCallback(
    (path: string) => {
      dispatch({ type: 'navigate', to: path })
      shell.pushLog(`Opened ${shortPath(path, state.home)}`, 'nav', ID)
    },
    [shell, state.home]
  )

  const selectItem = useCallback(
    (item: FsItem) => {
      dispatch({ type: 'select', path: item.path })
      shell.pushLog(`Selected ${item.name}`, 'select', ID)
    },
    [shell]
  )

  const openItem = useCallback(
    (item: FsItem) => {
      if (item.isDir) {
        go(item.path)
      } else if (/\.(md|markdown)$/i.test(item.name)) {
        shell.requestOpenDocument(item.path)
      } else {
        void window.fs.open(item.path)
      }
    },
    [go, shell]
  )

  const goUp = useCallback(() => {
    if (state.parent) go(state.parent)
  }, [state.parent, go])

  const goHome = useCallback(() => {
    if (state.home) go(state.home)
  }, [state.home, go])

  // --- the callable surface a real agent (Clio) reaches ---------------
  const actions: PaneAction[] = useMemo(
    () => [
      {
        name: 'open',
        describe: 'open a folder or file by name or tag number',
        params: ['target'],
        run: (args) => {
          const target = String(args.target ?? '')
          const hits = matchItems(target, visible)
          if (hits.length === 1) openItem(hits[0])
        }
      },
      {
        name: 'select',
        describe: 'select an item by name or tag number',
        params: ['target'],
        run: (args) => {
          const hits = matchItems(String(args.target ?? ''), visible)
          if (hits.length === 1) selectItem(hits[0])
        }
      },
      {
        name: 'filter',
        describe: 'narrow the folder to items matching text',
        params: ['text'],
        run: (args) => dispatch({ type: 'setQuery', value: String(args.text ?? '') })
      },
      { name: 'clearFilter', describe: 'remove the current filter', run: () => dispatch({ type: 'setQuery', value: '' }) },
      { name: 'up', describe: 'go up one level', run: goUp },
      { name: 'back', describe: 'go back', run: () => dispatch({ type: 'back' }) },
      { name: 'forward', describe: 'go forward', run: () => dispatch({ type: 'forward' }) },
      { name: 'goHome', describe: 'go to the home folder', run: goHome },
      {
        name: 'setView',
        describe: 'switch between grid and list',
        params: ['mode'],
        run: (args) => dispatch({ type: 'setView', value: args.mode === 'list' ? 'list' : 'grid' })
      },
      { name: 'toggleHidden', describe: 'show or hide dotfiles', run: () => dispatch({ type: 'toggleHidden' }) }
    ],
    [visible, openItem, selectItem, goUp, goHome]
  )

  const summary =
    (state.cwd ? shortPath(state.cwd, state.home) : 'loading') +
    ` · ${visible.length} item${visible.length === 1 ? '' : 's'}` +
    (selected ? ` · ${selected.name} selected` : '')

  const body = (
    <FileManagerBody
      state={state}
      visible={visible}
      selected={selected}
      onGo={go}
      onBack={() => dispatch({ type: 'back' })}
      onForward={() => dispatch({ type: 'forward' })}
      onUp={goUp}
      onQuery={(v) => dispatch({ type: 'setQuery', value: v })}
      onView={(v) => dispatch({ type: 'setView', value: v })}
      onToggleHidden={() => dispatch({ type: 'toggleHidden' })}
      onSelect={selectItem}
      onOpen={openItem}
      onReveal={(item) => void window.fs.reveal(item.path)}
    />
  )

  return { id: ID, title: 'Files', icon: '\u{1F4C1}', body, summary, actions }
}

export const fileManagerPane: PaneModule = {
  id: ID,
  title: 'Files',
  icon: '\u{1F4C1}',
  use: useFileManager
}
