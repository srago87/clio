// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import { unified } from 'unified'
import remarkParse from 'remark-parse'
import remarkGfm from 'remark-gfm'
import type { Root, RootContent } from 'mdast'

export type BlockKind = 'heading' | 'paragraph' | 'listItem' | 'code'

export interface Block {
  id: string
  tag: number
  kind: BlockKind
  text: string
  startLine: number
  endLine: number
  depth?: number
}

const parser = unified().use(remarkParse).use(remarkGfm)

/** Flatten all descendant text (and inline code) into one string, for matching. */
function textOf(node: unknown): string {
  const n = node as { type?: string; value?: string; children?: unknown[] }
  if (typeof n.value === 'string') return n.value
  if (Array.isArray(n.children)) return n.children.map(textOf).join('')
  return ''
}

function push(blocks: Block[], kind: BlockKind, node: RootContent, depth?: number): void {
  const pos = (node as { position?: { start: { line: number }; end: { line: number } } }).position
  blocks.push({
    id: `b${blocks.length}`,
    tag: blocks.length + 1,
    kind,
    text: kind === 'code' ? String((node as { value?: string }).value ?? '') : textOf(node),
    startLine: pos?.start.line ?? 0,
    endLine: pos?.end.line ?? 0,
    depth
  })
}

/** Addressable top-level blocks (headings, paragraphs, list items, code) in document
 *  order, tagged 1..N — the single source of truth for both the say-able tag shown
 *  next to each rendered block and `matchBlocks`'s target resolution. Other node
 *  types (blockquotes, tables, thematic breaks, raw HTML) still render, they're
 *  just not addressable yet. */
export function parseBlocks(markdown: string): Block[] {
  const tree = parser.parse(markdown) as Root
  const blocks: Block[] = []
  for (const node of tree.children) {
    if (node.type === 'heading') {
      push(blocks, 'heading', node, node.depth)
    } else if (node.type === 'paragraph') {
      push(blocks, 'paragraph', node)
    } else if (node.type === 'code') {
      push(blocks, 'code', node)
    } else if (node.type === 'list') {
      for (const item of node.children) push(blocks, 'listItem', item)
    }
  }
  return blocks
}

/** Resolve a spoken/typed target (a bare tag number, or text matching a block) to
 *  the blocks it could mean — same tiered exact/startsWith/contains/token-overlap
 *  algorithm as file-manager/interpret.ts's matchItems, over block text instead of
 *  item names. */
export function matchBlocks(target: string, blocks: Block[]): Block[] {
  const t = target.trim()
  const tagNum = t.match(/^(?:tag |block |number )?(\d{1,3})$/i)
  if (tagNum) {
    const block = blocks[parseInt(tagNum[1], 10) - 1]
    return block ? [block] : []
  }

  const n = t.toLowerCase().replace(/^(the|a|an) /, '').trim()
  if (!n) return []
  const exact = blocks.filter((b) => b.text.toLowerCase() === n)
  if (exact.length) return exact
  const starts = blocks.filter((b) => b.text.toLowerCase().startsWith(n))
  if (starts.length) return starts
  const contains = blocks.filter((b) => b.text.toLowerCase().includes(n))
  if (contains.length) return contains
  const toks = n.split(/\s+/).filter(Boolean)
  return blocks.filter((b) => {
    const hay = b.text.toLowerCase()
    return toks.length > 1 && toks.every((tok) => hay.includes(tok))
  })
}
