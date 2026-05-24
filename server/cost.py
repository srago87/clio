from collections import defaultdict
from dataclasses import dataclass, field

# claude-sonnet-4-6 pricing (USD per token)
_INPUT   = 3.00  / 1_000_000
_OUTPUT  = 15.00 / 1_000_000
_CACHE_W = 3.75  / 1_000_000
_CACHE_R = 0.30  / 1_000_000


def usd_from_usage(usage) -> float:
    return (
        getattr(usage, "input_tokens", 0)                  * _INPUT   +
        getattr(usage, "output_tokens", 0)                 * _OUTPUT  +
        getattr(usage, "cache_creation_input_tokens", 0)   * _CACHE_W +
        getattr(usage, "cache_read_input_tokens", 0)       * _CACHE_R
    )


def log_usage(label: str, usage) -> None:
    """Log cost for a single API call outside of a session (e.g. consolidation)."""
    cost = usd_from_usage(usage)
    inp = getattr(usage, "input_tokens", 0)
    out = getattr(usage, "output_tokens", 0)
    cw  = getattr(usage, "cache_creation_input_tokens", 0)
    cr  = getattr(usage, "cache_read_input_tokens", 0)
    print(f"[cost] {label}: ${cost:.4f} ({inp}in / {out}out / {cw}cw / {cr}cr)")


@dataclass
class _Bucket:
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
        return (
            self.input_tokens       * _INPUT   +
            self.output_tokens      * _OUTPUT  +
            self.cache_write_tokens * _CACHE_W +
            self.cache_read_tokens  * _CACHE_R
        )


class SessionCostTracker:
    def __init__(self):
        self._buckets: dict[str, _Bucket] = defaultdict(_Bucket)

    def record(self, label: str, usage) -> None:
        self._buckets[label].add(usage)

    def report(self) -> str:
        if not self._buckets:
            return "[cost] no API calls recorded this session"

        total = sum(b.usd for b in self._buckets.values())
        lines = ["[cost] ── session cost summary ──────────────────"]
        for label in sorted(self._buckets):
            b = self._buckets[label]
            suffix = f"×{b.calls}" if b.calls > 1 else ""
            lines.append(
                f"[cost]   {label}{suffix}: ${b.usd:.4f}"
                f"  ({b.input_tokens}in / {b.output_tokens}out"
                f"{f' / {b.cache_write_tokens}cw' if b.cache_write_tokens else ''}"
                f"{f' / {b.cache_read_tokens}cr' if b.cache_read_tokens else ''})"
            )
        lines.append(f"[cost]   TOTAL: ${total:.4f}")
        lines.append("[cost] ─────────────────────────────────────────")
        return "\n".join(lines)
