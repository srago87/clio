# Clio's Display — prototype

An environment designed around voice-driven work. Clio is the
orchestrator; this is the display it drives. The bet: a GUI that's a render
of typed state, where everything is nameable and Clio acts on it by name —
instead of reverse-engineering speakability out of ordinary apps.

## Run it

```bash
npm install
npm run dev      # live-reload dev build
npm run build    # production build into out/
npm run preview  # run the production build
```

Requires a display. After a fresh `npm install`, Electron's sandbox helper needs
one-time setup:

```bash
sudo chown root:root node_modules/electron/dist/chrome-sandbox
sudo chmod 4755 node_modules/electron/dist/chrome-sandbox
```

## Shape

```
src/main/index.ts          Electron main — directory listing + chokidar watcher over IPC
src/preload/index.ts       the window.fs bridge

src/renderer/src/
  shell/                   the environment
    Shell.tsx              layout, pane registry, Esc -> overview
    shellStore.ts          shell state: focused pane, overview open/closed, history log
    types.ts               the pane contract — PaneModule / PaneHandle / PaneAction
    agent.ts               the seam to Clio: getState / listActions / invokeAction / subscribe
    ShellContext.ts        what a pane may touch on the shell (focus, pushLog, notifyChange,
                           requestOpenDocument for cross-pane handoff)
    Overview.tsx           the home screen — overview of every pane, and how you pick one
    ShellBar.tsx           top bar: Overview button + pane tabs
    AmbientRail.tsx        always-on status column (History now; Running Work later)
    useClioLink.ts         WebSocket link answering Clio's getState/listActions/invokeAction

  panes/
    file-manager/          pane 1
      index.tsx            the PaneModule: use() hook -> live PaneHandle
      FileManagerBody.tsx  the pane's own UI (toolbar, sidebar, file view, selection bar)
      store.ts             pane-local state + reducer
      interpret.ts         matchItems() — resolves a name/tag argument against what's on screen
      components/          Sidebar, Toolbar, FileView, SelectionBar

    document/              pane 2 — read-only markdown viewer
      index.tsx            the PaneModule; opens via requestOpenDocument or the `open` action
      blocks.ts            parseBlocks()/matchBlocks() — addressable headings/paragraphs/
                           list items/code blocks, single source of truth for both the
                           rendered tag numbers and the `select` action's target matching
      store.ts             pane-local state + reducer
      DocumentBody.tsx      react-markdown render with numbered/selectable block overrides
```

## How a pane plugs in

A pane is a `PaneModule` — an id, a title, an icon, and a `use()` hook. The shell
calls `use()` unconditionally each render; it returns a `PaneHandle`:

- `body` — what shows when the pane is focused
- `summary` — one line for the overview
- `actions` — the callable surface Clio invokes by name (via `display_invoke_action`)

The shell owns everything cross-cutting: the focused pane, the say-able tag
namespace, the history log, and the driver seam. A pane never enumerates its
verbs to the user — it only helps name things.

## Driver seam

`shell/agent.ts` defines `AgentDriver` and `createLocalDriver(shell)`. Clio
connects through `createLocalDriver` over a local socket (`useClioLink.ts`) —
`getState()` feeds its context, `listActions()` is its tool list, `invokeAction()`
is how it acts. Invoking a pane's own action brings that pane into focus first,
so the effect is always visible.

## In it now

- **Overview / home screen** — the zero state; overview of panes, click or Clio invokes
  `shell.focusPane` to enter one.
- **File manager pane** — live filesystem view (chokidar), starts at `~`, up to `/`.
  Say-able number tags, central filter, grid/list, loud selection, disk-change flash.
  Double-clicking a `.md` file hands off to the document pane instead of shelling
  out to the OS.
- **Document pane** — read-only live-rendered markdown (chokidar-watched single
  file), numbered/selectable blocks. No editing yet — that's the next layer once
  this render/addressing pipeline is proven out.
- **History rail** — running log of navigations, selections, disk changes.
- **Clio link** — `useClioLink.ts` answers Clio's getState/listActions/invokeAction
  calls over the local WebSocket; see `server/main.py`'s `DisplayLink`.

## Not here yet (by design)

- No microphone in this GUI — Clio is the only voice surface (via the phone); see
  the "Clio's Display" section of `project-clio.md` for why.
- No file mutations from the UI. Clio does file ops; this is the picture of them.
- Document pane editing (retitle a heading, insert a paragraph) — viewing first.
