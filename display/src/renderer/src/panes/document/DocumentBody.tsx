// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import { useMemo } from 'react'
import type { ComponentType, JSX, ReactNode } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ExtraProps } from 'react-markdown'
import type { State } from './store'

interface Props {
  state: State
  onSelect: (id: string | null) => void
}

/** Say-able tag shown beside an addressable block. */
function Tag({ n, active }: { n: number; active: boolean }): React.JSX.Element {
  return (
    <span
      className={`mr-2 inline-block min-w-[20px] select-none rounded-md px-1 text-center align-top text-[11px] font-semibold tabular-nums ${
        active ? 'bg-sky-400 text-slate-900' : 'bg-white/10 text-white/60'
      }`}
    >
      {n}
    </span>
  )
}

/** No typography plugin here — base look per tag, kept plain on purpose. */
const BASE_STYLE: Record<string, string> = {
  h1: 'mb-3 mt-6 text-[22px] font-semibold text-white',
  h2: 'mb-2 mt-5 text-[18px] font-semibold text-white',
  h3: 'mb-2 mt-4 text-[16px] font-semibold text-white/90',
  h4: 'mb-2 mt-4 text-[14px] font-semibold text-white/90',
  h5: 'mb-1 mt-3 text-[13px] font-semibold text-white/80',
  h6: 'mb-1 mt-3 text-[13px] font-semibold text-white/70',
  p: 'mb-3',
  li: 'mb-1 ml-5 list-item list-disc marker:text-white/30',
  pre: 'mb-3 overflow-auto rounded-lg bg-black/30 p-3 font-mono text-[12px] text-white/80'
}

type BlockProps = ExtraProps & { children?: ReactNode }

export default function DocumentBody({ state, onSelect }: Props): React.JSX.Element {
  const lineToBlock = useMemo(
    () => new Map(state.blocks.map((b) => [b.startLine, { tag: b.tag, id: b.id }])),
    [state.blocks]
  )

  const makeBlock = useMemo(() => {
    return (tagName: keyof JSX.IntrinsicElements): ComponentType<BlockProps> => {
      const base = BASE_STYLE[tagName] ?? ''
      function Block({ node, children }: BlockProps): React.JSX.Element {
        const line = node?.position?.start.line
        const found = line !== undefined ? lineToBlock.get(line) : undefined
        const Tagname = tagName
        if (!found) {
          return <Tagname className={base}>{children}</Tagname>
        }
        const active = found.id === state.selectedBlockId
        return (
          <Tagname
            onClick={() => onSelect(active ? null : found.id)}
            className={`${base} -mx-1 cursor-default rounded-md px-1 transition-colors ${
              active ? 'bg-sky-500/15 ring-1 ring-sky-400/50' : 'hover:bg-white/5'
            }`}
          >
            <Tag n={found.tag} active={active} />
            {children}
          </Tagname>
        )
      }
      return Block
    }
  }, [lineToBlock, state.selectedBlockId, onSelect])

  const components = useMemo(
    () => ({
      h1: makeBlock('h1'),
      h2: makeBlock('h2'),
      h3: makeBlock('h3'),
      h4: makeBlock('h4'),
      h5: makeBlock('h5'),
      h6: makeBlock('h6'),
      p: makeBlock('p'),
      li: makeBlock('li'),
      pre: makeBlock('pre')
    }),
    [makeBlock]
  )

  if (!state.path) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-white/35">
        No document open — open a .md file from the file manager
      </div>
    )
  }

  if (state.loading) {
    return <div className="flex flex-1 items-center justify-center text-sm text-white/35">Loading…</div>
  }

  if (state.error) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-red-300/80">
        Couldn't read this file — {state.error}
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-auto p-6">
      <div className="max-w-3xl text-[14px] leading-relaxed text-white/85">
        <Markdown remarkPlugins={[remarkGfm]} components={components}>
          {state.content}
        </Markdown>
      </div>
    </div>
  )
}
