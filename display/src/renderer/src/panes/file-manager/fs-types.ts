// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

export interface FsItem {
  name: string
  path: string
  isDir: boolean
  size: number
  mtime: number
  ext: string
  hidden: boolean
}

export interface DirListing {
  path: string
  parent: string | null
  items: FsItem[]
}
