"""
Console progress bar for the long-running loops (stdlib only, no deps).

Writes to stderr so it never pollutes piped/captured stdout (reports, json). On a TTY it
is a single \r-updated line with count, percent, elapsed and ETA; when stderr is redirected
to a file it degrades to one plain line per ~10% so logs stay readable.

    bar = ProgressBar(total=336, label="scoring")
    ...
    bar.advance(note=row_id)     # call once per completed item (any task/thread order)
    ...
    bar.close()

In asyncio code, `asyncio.create_task(bar.tick())` keeps the elapsed clock moving while
the first slow call (Sandbox 0.2 RPS) is still in flight — the "is it alive?" signal.
"""
import asyncio
import sys
import time


def _fmt(seconds: float) -> str:
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"
    if seconds >= 60:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds}s"


class ProgressBar:
    def __init__(self, total: int, label: str = "", width: int = 24,
                 stream=None, min_interval: float = 0.1):
        self.total = max(int(total), 0)
        self.label = label
        self.done = 0
        self.width = width
        self.stream = stream if stream is not None else sys.stderr
        self.min_interval = min_interval
        self.start = time.monotonic()
        self._note = ""
        self._last_render = 0.0
        self._last_len = 0
        self._next_pct = 10          # non-TTY: log a line every 10%
        self._tty = getattr(self.stream, "isatty", lambda: False)()
        if self.total:
            self._render(force=True)

    def advance(self, n: int = 1, note: str = "") -> None:
        self.done += n
        if note:
            self._note = note
        self._render(force=self.done >= self.total)

    async def tick(self, interval: float = 1.0) -> None:
        """Re-render every `interval` s so elapsed/ETA move while calls are in flight.
        Run as a background task and cancel (or let it exit) when the gather returns."""
        while self.done < self.total:
            await asyncio.sleep(interval)
            self._render(force=True)

    def close(self) -> None:
        """Finish the line so the next print starts on a clean row."""
        if self.total and self._tty:
            self._render(force=True)
            self.stream.write("\n")
            self.stream.flush()

    def _line(self) -> str:
        elapsed = time.monotonic() - self.start
        pct = self.done / self.total if self.total else 0.0
        filled = int(self.width * pct)
        bar = "█" * filled + "░" * (self.width - filled)
        eta = ""
        if 0 < self.done < self.total:
            eta = f" | ETA {_fmt(elapsed / self.done * (self.total - self.done))}"
        note = f" | {self._note}" if self._note and self.done < self.total else ""
        label = f"{self.label} " if self.label else ""
        return f"{label}{bar} {self.done}/{self.total} ({pct * 100:3.0f}%) | {_fmt(elapsed)}{eta}{note}"

    def _render(self, force: bool = False) -> None:
        if not self.total:
            return
        if self._tty:
            now = time.monotonic()
            if not force and now - self._last_render < self.min_interval:
                return
            line = self._line()
            pad = " " * max(0, self._last_len - len(line))
            self.stream.write("\r" + line + pad)
            self.stream.flush()
            self._last_len = len(line)
            self._last_render = now
        else:
            pct = self.done / self.total * 100
            if pct >= self._next_pct or self.done >= self.total:
                self.stream.write(self._line() + "\n")
                self.stream.flush()
                while self._next_pct <= pct:
                    self._next_pct += 10
