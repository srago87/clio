// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import Sidebar from './components/Sidebar'
import Toolbar from './components/Toolbar'
import FileView from './components/FileView'
import SelectionBar from './components/SelectionBar'
import type { FsItem } from './fs-types'
import type { State, ViewMode } from './store'

export interface FileManagerBodyProps {
  state: State
  visible: FsItem[]
  selected: FsItem | null
  onGo: (path: string) => void
  onBack: () => void
  onForward: () => void
  onUp: () => void
  onQuery: (value: string) => void
  onView: (v: ViewMode) => void
  onToggleHidden: () => void
  onSelect: (item: FsItem) => void
  onOpen: (item: FsItem) => void
  onReveal: (item: FsItem) => void
}

export default function FileManagerBody({
  state,
  visible,
  selected,
  onGo,
  onBack,
  onForward,
  onUp,
  onQuery,
  onView,
  onToggleHidden,
  onSelect,
  onOpen,
  onReveal
}: FileManagerBodyProps): React.JSX.Element {
  return (
    <div className="flex h-full flex-col">
      <Toolbar
        home={state.home}
        cwd={state.cwd}
        canBack={state.back.length > 0}
        canForward={state.forward.length > 0}
        canUp={!!state.parent}
        query={state.query}
        view={state.view}
        showHidden={state.showHidden}
        onBack={onBack}
        onForward={onForward}
        onUp={onUp}
        onCrumb={(p) => onGo(p)}
        onQuery={onQuery}
        onView={onView}
        onToggleHidden={onToggleHidden}
      />
      <div className="flex min-h-0 flex-1">
        <Sidebar home={state.home} cwd={state.cwd} onGo={(p) => onGo(p)} />
        <div className="flex min-w-0 flex-1 flex-col">
          <SelectionBar
            selected={selected}
            home={state.home}
            count={visible.length}
            onOpen={onOpen}
            onReveal={onReveal}
          />
          <FileView
            items={visible}
            view={state.view}
            selectedPath={state.selectedPath}
            flashPath={state.flashPath}
            query={state.query}
            onSelect={onSelect}
            onOpen={onOpen}
          />
        </div>
      </div>
    </div>
  )
}
