// Clio — Copyright (C) 2026 Sean Rago
// Licensed under the GNU Affero General Public License v3.0 or later.
// See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

import { iconFor } from '../format'

interface Place {
  label: string
  path: string
  icon: string
}

interface Props {
  home: string
  cwd: string
  onGo: (path: string, label: string) => void
}

export default function Sidebar({ home, cwd, onGo }: Props): React.JSX.Element {
  const places: Place[] = [
    { label: 'Home', path: home, icon: '\u{1F3E0}' },
    { label: 'Desktop', path: `${home}/Desktop`, icon: '\u{1F5A5}\u{FE0F}' },
    { label: 'Documents', path: `${home}/Documents`, icon: iconFor({ isDir: true, ext: '' }) },
    { label: 'Downloads', path: `${home}/Downloads`, icon: '\u{2B07}\u{FE0F}' },
    { label: 'Pictures', path: `${home}/Pictures`, icon: '\u{1F5BC}\u{FE0F}' },
    { label: 'Filesystem', path: '/', icon: '\u{1F4BD}' }
  ]

  return (
    <nav className="flex w-52 shrink-0 flex-col gap-0.5 border-r border-white/8 bg-black/20 px-2 py-3">
      <div className="px-2 pb-2 text-[11px] font-semibold uppercase tracking-wider text-white/35">Places</div>
      {places.map((p) => {
        const active = cwd === p.path
        return (
          <button
            key={p.label}
            onClick={() => onGo(p.path, p.label)}
            className={`flex items-center gap-2.5 rounded-lg px-2.5 py-[7px] text-left text-[13px] transition-colors ${
              active ? 'bg-sky-500/20 text-sky-200' : 'text-white/70 hover:bg-white/6'
            }`}
          >
            <span className="text-base leading-none">{p.icon}</span>
            <span className="truncate">{p.label}</span>
          </button>
        )
      })}
    </nav>
  )
}
