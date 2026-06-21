"use strict";

const statusDot   = document.getElementById("status-dot");
const statusLabel = document.getElementById("status-label");
const content     = document.getElementById("content");
const emptyState  = document.getElementById("empty-state");
const clearBtn    = document.getElementById("clear-btn");
const scrollBtn   = document.getElementById("scroll-btn");

let ws = null;
let reconnectTimer = null;
let userScrolled = false;

// ── WebSocket ──────────────────────────────────────────────────────────────

function connect() {
  const token = window.CLIO_TOKEN || "";
  const proto  = location.protocol === "https:" ? "wss" : "ws";
  const url    = `${proto}://${location.host}/ws/diff?token=${token}`;

  ws = new WebSocket(url);

  ws.addEventListener("open", () => {
    setStatus("connected", "Live");
    clearTimeout(reconnectTimer);
  });

  ws.addEventListener("close", () => {
    setStatus("disconnected", "Disconnected — reconnecting…");
    reconnectTimer = setTimeout(connect, 3000);
  });

  ws.addEventListener("error", () => {
    ws.close();
  });

  ws.addEventListener("message", (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === "file_diff") {
        renderDiff(msg.path, msg.old, msg.new);
      }
    } catch (e) {
      console.error("diff.js: bad message", e);
    }
  });
}

function setStatus(cls, label) {
  statusDot.className = cls;
  statusLabel.textContent = label;
}

// ── Diff computation ───────────────────────────────────────────────────────

/**
 * Minimal Myers diff on lines. Returns an array of operations:
 * { op: "eq"|"ins"|"del", lines: string[] }
 */
function diffLines(oldText, newText) {
  const a = oldText === "" ? [] : oldText.split("\n");
  const b = newText === "" ? [] : newText.split("\n");

  // Remove trailing empty line caused by trailing newline
  if (a.length && a[a.length - 1] === "") a.pop();
  if (b.length && b[b.length - 1] === "") b.pop();

  const n = a.length, m = b.length;
  const max = n + m;
  if (max === 0) return [];

  // Myers algorithm — forward pass
  const V = new Int32Array(2 * max + 2);
  const trace = [];

  outer: for (let d = 0; d <= max; d++) {
    trace.push(V.slice());
    for (let k = -d; k <= d; k += 2) {
      let x;
      if (k === -d || (k !== d && V[k - 1 + max] < V[k + 1 + max])) {
        x = V[k + 1 + max];
      } else {
        x = V[k - 1 + max] + 1;
      }
      let y = x - k;
      while (x < n && y < m && a[x] === b[y]) { x++; y++; }
      V[k + max] = x;
      if (x >= n && y >= m) break outer;
    }
  }

  // Backtrack
  const ops = [];
  let x = n, y = m;
  for (let d = trace.length - 1; d >= 0; d--) {
    const v = trace[d];
    const k = x - y;
    let prevK;
    if (k === -d || (k !== d && v[k - 1 + max] < v[k + 1 + max])) {
      prevK = k + 1;
    } else {
      prevK = k - 1;
    }
    const prevX = v[prevK + max];
    const prevY = prevX - prevK;

    while (x > prevX + 1 && y > prevY + 1) { ops.push({ op: "eq",  line: a[--x - 1] }); y--; }
    if (d > 0) {
      if (x === prevX) {
        ops.push({ op: "ins", line: b[--y] });
      } else {
        ops.push({ op: "del", line: a[--x] });
      }
    }
    while (x > prevX && y > prevY) { ops.push({ op: "eq", line: a[--x - 1] }); y--; }
    x = prevX;
    y = prevY;
  }

  ops.reverse();
  return ops;
}

/**
 * Group raw ops into hunks (only show context lines around changes).
 * Returns array of hunks: { oldStart, newStart, lines: [{op, line, oldLn, newLn}] }
 */
const CTX = 3; // context lines around each change

function buildHunks(ops) {
  // Assign line numbers
  let oldLn = 1, newLn = 1;
  const annotated = ops.map(op => {
    const entry = { op: op.op, line: op.line, oldLn: null, newLn: null };
    if (op.op === "eq")  { entry.oldLn = oldLn++; entry.newLn = newLn++; }
    if (op.op === "del") { entry.oldLn = oldLn++; }
    if (op.op === "ins") { entry.newLn = newLn++; }
    return entry;
  });

  const hasChange = annotated.some(e => e.op !== "eq");
  if (!hasChange) return [];

  // Find indices of changed lines
  const changed = new Set();
  annotated.forEach((e, i) => { if (e.op !== "eq") changed.add(i); });

  // Expand to include context
  const inHunk = new Set();
  changed.forEach(i => {
    for (let j = Math.max(0, i - CTX); j <= Math.min(annotated.length - 1, i + CTX); j++) {
      inHunk.add(j);
    }
  });

  // Build hunks as contiguous sequences
  const hunks = [];
  let current = null;
  for (let i = 0; i < annotated.length; i++) {
    if (inHunk.has(i)) {
      if (!current) {
        current = { oldStart: annotated[i].oldLn ?? 1, newStart: annotated[i].newLn ?? 1, lines: [] };
      }
      current.lines.push(annotated[i]);
    } else {
      if (current) { hunks.push(current); current = null; }
    }
  }
  if (current) hunks.push(current);

  return hunks;
}

// ── Rendering ─────────────────────────────────────────────────────────────

function escape(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function formatTime(date) {
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function renderDiff(path, oldText, newText) {
  const isNew = oldText === "";
  const ops   = diffLines(oldText, newText);
  const hunks = isNew ? null : buildHunks(ops);

  let addCount = 0, delCount = 0;
  ops.forEach(o => { if (o.op === "ins") addCount++; if (o.op === "del") delCount++; });

  // Card
  const card = document.createElement("div");
  card.className = "diff-card";

  // Header
  const header = document.createElement("div");
  header.className = "diff-card-header";
  header.innerHTML = `
    <span class="file-path">${escape(path)}</span>
    <span class="diff-stats">
      ${addCount > 0 ? `<span class="stat-add">+${addCount}</span>` : ""}
      ${addCount > 0 && delCount > 0 ? " " : ""}
      ${delCount > 0 ? `<span class="stat-del">-${delCount}</span>` : ""}
    </span>
    <span class="diff-time">${formatTime(new Date())}</span>
  `;
  card.appendChild(header);

  const body = document.createElement("div");
  body.className = "diff-body";

  if (isNew) {
    // New file — show all lines as additions
    const notice = document.createElement("div");
    notice.className = "diff-new-file";
    notice.textContent = "new file";
    body.appendChild(notice);

    const lines = newText.split("\n");
    if (lines.length && lines[lines.length - 1] === "") lines.pop();
    lines.forEach((line, i) => {
      body.appendChild(makeLine("add", "+", i + 1, null, line));
    });
  } else if (!hunks || hunks.length === 0) {
    // No changes (shouldn't happen but handle gracefully)
    const notice = document.createElement("div");
    notice.className = "diff-hunk-sep";
    notice.textContent = "no changes";
    body.appendChild(notice);
  } else {
    hunks.forEach((hunk, hi) => {
      // Hunk header
      const sep = document.createElement("div");
      sep.className = "diff-hunk-sep";
      sep.textContent = `@@ -${hunk.oldStart} +${hunk.newStart} @@`;
      body.appendChild(sep);

      hunk.lines.forEach(entry => {
        if (entry.op === "eq") {
          body.appendChild(makeLine("ctx", " ", entry.oldLn, entry.newLn, entry.line));
        } else if (entry.op === "ins") {
          body.appendChild(makeLine("add", "+", null, entry.newLn, entry.line));
        } else {
          body.appendChild(makeLine("del", "-", entry.oldLn, null, entry.line));
        }
      });
    });
  }

  card.appendChild(body);

  // Remove empty state
  if (emptyState.parentNode === content) {
    content.removeChild(emptyState);
  }

  content.appendChild(card);

  // Auto-scroll if user hasn't scrolled up
  if (!userScrolled) {
    card.scrollIntoView({ behavior: "smooth", block: "end" });
  } else {
    scrollBtn.style.display = "block";
  }
}

function makeLine(cls, prefix, oldLn, newLn, text) {
  const div = document.createElement("div");
  div.className = `diff-line ${cls}`;

  const lnSpan = document.createElement("span");
  lnSpan.className = "ln";
  lnSpan.textContent = (cls === "add" ? (newLn ?? "") : (oldLn ?? ""));
  div.appendChild(lnSpan);

  const pfx = document.createElement("span");
  pfx.className = "prefix";
  pfx.textContent = prefix;

  const code = document.createElement("span");
  code.className = "code";
  // Use textContent (not innerHTML) to avoid XSS
  code.textContent = text;

  const wrapper = document.createElement("span");
  wrapper.style.display = "contents";
  wrapper.appendChild(pfx);
  wrapper.appendChild(code);
  div.appendChild(wrapper);

  return div;
}

// ── Controls ──────────────────────────────────────────────────────────────

clearBtn.addEventListener("click", () => {
  while (content.firstChild) content.removeChild(content.firstChild);
  content.appendChild(emptyState);
  userScrolled = false;
  scrollBtn.style.display = "none";
});

scrollBtn.addEventListener("click", () => {
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
  userScrolled = false;
  scrollBtn.style.display = "none";
});

window.addEventListener("scroll", () => {
  const nearBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 100;
  if (nearBottom) {
    userScrolled = false;
    scrollBtn.style.display = "none";
  } else {
    userScrolled = true;
  }
});

// ── Init ──────────────────────────────────────────────────────────────────

connect();
