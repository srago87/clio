// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import type { FsItem } from './fs-types'

export function iconFor(item: Pick<FsItem, 'isDir' | 'ext'>): string {
  if (item.isDir) return '\u{1F4C1}' // folder
  const e = item.ext
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'heic', 'ico'].includes(e)) return '\u{1F5BC}\u{FE0F}'
  if (e === 'pdf') return '\u{1F4D5}'
  if (['md', 'txt', 'rtf', 'doc', 'docx', 'odt', 'pages'].includes(e)) return '\u{1F4C4}'
  if (
    ['js', 'ts', 'tsx', 'jsx', 'py', 'rs', 'go', 'c', 'cpp', 'h', 'hpp', 'java', 'rb', 'sh', 'json', 'html', 'css', 'toml', 'yml', 'yaml'].includes(e)
  )
    return '\u{1F4DC}'
  if (['zip', 'tar', 'gz', 'bz2', '7z', 'rar', 'xz', 'tgz'].includes(e)) return '\u{1F5DC}\u{FE0F}'
  if (['mp3', 'wav', 'flac', 'ogg', 'm4a', 'aac'].includes(e)) return '\u{1F3B5}'
  if (['mp4', 'mkv', 'mov', 'avi', 'webm', 'm4v'].includes(e)) return '\u{1F3AC}'
  if (['xls', 'xlsx', 'csv', 'ods'].includes(e)) return '\u{1F4CA}'
  if (['ppt', 'pptx', 'key', 'odp'].includes(e)) return '\u{1F4D1}'
  return '\u{1F4C4}'
}

export function formatSize(bytes: number, isDir: boolean): string {
  if (isDir) return '—'
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)))
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

export function formatDate(ms: number): string {
  if (!ms) return '—'
  return new Date(ms).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

/** Collapse the user's home directory to ~ for display. */
export function shortPath(full: string, home: string): string {
  if (home && (full === home || full.startsWith(home + '/'))) return '~' + full.slice(home.length)
  return full
}

/** Split a path into { label, path } crumbs, home-aware. */
export function crumbs(full: string, home: string): { label: string; path: string }[] {
  const out: { label: string; path: string }[] = []
  if (home && (full === home || full.startsWith(home + '/'))) {
    out.push({ label: '~', path: home })
    const rest = full.slice(home.length).split('/').filter(Boolean)
    let acc = home
    for (const seg of rest) {
      acc += '/' + seg
      out.push({ label: seg, path: acc })
    }
    return out
  }
  out.push({ label: '/', path: '/' })
  const rest = full.split('/').filter(Boolean)
  let acc = ''
  for (const seg of rest) {
    acc += '/' + seg
    out.push({ label: seg, path: acc })
  }
  return out
}
