# Clio — Copyright (C) 2026 Sean Rago
# Licensed under the GNU Affero General Public License v3.0 or later.
# See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

from dataclasses import dataclass

PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {
        "input":       1.00 / 1_000_000,
        "output":      5.00 / 1_000_000,
        "cache_write": 1.25 / 1_000_000,
        "cache_read":  0.10 / 1_000_000,
    },
    "claude-haiku-4-5-20251001": {
        "input":       1.00 / 1_000_000,
        "output":      5.00 / 1_000_000,
        "cache_write": 1.25 / 1_000_000,
        "cache_read":  0.10 / 1_000_000,
    },
    "claude-sonnet-4-6": {
        "input":       3.00 / 1_000_000,
        "output":     15.00 / 1_000_000,
        "cache_write": 3.75 / 1_000_000,
        "cache_read":  0.30 / 1_000_000,
    },
    "claude-opus-4-8": {
        "input":       5.00 / 1_000_000,
        "output":     25.00 / 1_000_000,
        "cache_write": 6.25 / 1_000_000,
        "cache_read":  0.50 / 1_000_000,
    },
}

_FALLBACK = PRICING["claude-sonnet-4-6"]


def _price(model: str) -> dict[str, float]:
    return PRICING.get(model, _FALLBACK)


def usd_from_usage(usage, model: str = "claude-sonnet-4-6") -> float:
    p = _price(model)
    return (
        getattr(usage, "input_tokens", 0)                * p["input"]       +
        getattr(usage, "output_tokens", 0)               * p["output"]      +
        getattr(usage, "cache_creation_input_tokens", 0) * p["cache_write"] +
        getattr(usage, "cache_read_input_tokens", 0)     * p["cache_read"]
    )


def log_usage(label: str, usage, model: str = "claude-sonnet-4-6") -> None:
    """Log cost for a single API call outside of a session (e.g. consolidation)."""
    cost = usd_from_usage(usage, model)
    inp = getattr(usage, "input_tokens", 0)
    out = getattr(usage, "output_tokens", 0)
    cw  = getattr(usage, "cache_creation_input_tokens", 0)
    cr  = getattr(usage, "cache_read_input_tokens", 0)
    short = model.replace("claude-", "").replace("-20251001", "")
    print(f"[cost] {label} ({short}): ${cost:.4f} ({inp}in / {out}out / {cw}cw / {cr}cr)")


@dataclass
class _Bucket:
    model: str = "claude-sonnet-4-6"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    calls: int = 0

    def add(self, usage) -> None:
        self.input_tokens       += getattr(usage, "input_tokens", 0)
        self.output_tokens      += getattr(usage, "output_tokens", 0)
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0)
        self.cache_read_tokens  += getattr(usage, "cache_read_input_tokens", 0)
        self.calls += 1

    @property
    def usd(self) -> float:
        p = _price(self.model)
        return (
            self.input_tokens       * p["input"]       +
            self.output_tokens      * p["output"]      +
            self.cache_write_tokens * p["cache_write"] +
            self.cache_read_tokens  * p["cache_read"]
        )


class SessionCostTracker:
    def __init__(self):
        self._buckets: dict[str, _Bucket] = {}

    def record(self, label: str, usage, model: str = "claude-sonnet-4-6") -> None:
        if label not in self._buckets:
            self._buckets[label] = _Bucket(model=model)
        self._buckets[label].add(usage)

    @property
    def total_usd(self) -> float:
        return sum(b.usd for b in self._buckets.values())

    @property
    def total_input_tokens(self) -> int:
        return sum(
            b.input_tokens + b.cache_write_tokens + b.cache_read_tokens
            for b in self._buckets.values()
        )

    def report(self) -> str:
        if not self._buckets:
            return "[cost] no API calls recorded this session"

        total = self.total_usd
        lines = ["[cost] ── session cost summary ──────────────────"]
        for label in sorted(self._buckets):
            b = self._buckets[label]
            suffix = f"×{b.calls}" if b.calls > 1 else ""
            short = b.model.replace("claude-", "").replace("-20251001", "")
            lines.append(
                f"[cost]   {label}{suffix} ({short}): ${b.usd:.4f}"
                f"  ({b.input_tokens}in / {b.output_tokens}out"
                f"{f' / {b.cache_write_tokens}cw' if b.cache_write_tokens else ''}"
                f"{f' / {b.cache_read_tokens}cr' if b.cache_read_tokens else ''})"
            )
        lines.append(f"[cost]   TOTAL: ${total:.4f}")
        lines.append("[cost] ─────────────────────────────────────────")
        return "\n".join(lines)
