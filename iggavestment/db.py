"""
Lightweight in-memory accumulator — no SQLite required.
Events and scores are held in Python lists for the lifetime of one pipeline run.
Persistence is state.json + audit.jsonl; no DB file to manage or migrate.
"""
from __future__ import annotations

from .normalize import Event, Score


class RunAccumulator:
    """Holds events and scores for a single pipeline invocation."""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self.scores: list[Score] = []
        self._seen_hashes: set[str] = set()

    def add_event(self, event: Event) -> bool:
        """Returns True if new, False if duplicate."""
        if event.raw_hash in self._seen_hashes:
            return False
        self._seen_hashes.add(event.raw_hash)
        self.events.append(event)
        return True

    def add_events(self, events: list[Event]) -> int:
        return sum(1 for e in events if self.add_event(e))

    def add_scores(self, scores: list[Score]) -> None:
        self.scores.extend(scores)
