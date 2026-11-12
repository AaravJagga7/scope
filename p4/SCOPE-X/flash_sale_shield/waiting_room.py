"""Virtual waiting room: a bounded FIFO queue with wait-time accounting."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, List, Optional


@dataclass
class Ticket:
    request_id: int
    arrival: float          # time the request reached the shield
    enqueued: float         # time it entered the waiting room
    payload: Any = None


class WaitingRoom:
    def __init__(self, capacity: int, max_wait_s: float = float("inf")):
        if capacity < 0:
            raise ValueError("capacity must be >= 0")
        self.capacity = int(capacity)
        self.max_wait_s = float(max_wait_s)
        self._q: Deque[Ticket] = deque()
        # counters
        self.enqueued = 0
        self.dequeued = 0
        self.rejected_full = 0
        self.expired = 0
        self.max_length_seen = 0

    def __len__(self) -> int:
        return len(self._q)

    @property
    def fill_ratio(self) -> float:
        return 1.0 if self.capacity == 0 else len(self._q) / self.capacity

    def is_full(self) -> bool:
        return len(self._q) >= self.capacity

    def offer(self, ticket: Ticket) -> bool:
        """Enqueue at the tail. Returns False (and counts it) when full."""
        if self.is_full():
            self.rejected_full += 1
            return False
        self._q.append(ticket)
        self.enqueued += 1
        self.max_length_seen = max(self.max_length_seen, len(self._q))
        return True

    def peek(self) -> Optional[Ticket]:
        return self._q[0] if self._q else None

    def poll(self) -> Optional[Ticket]:
        """Remove and return the head (the oldest waiting request)."""
        if not self._q:
            return None
        self.dequeued += 1
        return self._q.popleft()

    def expire(self, now: float) -> List[Ticket]:
        """Drop requests that have waited longer than ``max_wait_s``.

        FIFO means the oldest requests are at the head, so we only look there.
        """
        out: List[Ticket] = []
        while self._q and now - self._q[0].enqueued > self.max_wait_s:
            out.append(self._q.popleft())
            self.expired += 1
        return out

    def next_expiry(self) -> Optional[float]:
        if not self._q or self.max_wait_s == float("inf"):
            return None
        return self._q[0].enqueued + self.max_wait_s

    def resize(self, capacity: int) -> None:
        """Shrinking never evicts already-waiting customers; it only stops
        new arrivals until the queue drains below the new capacity."""
        if capacity < 0:
            raise ValueError("capacity must be >= 0")
        self.capacity = int(capacity)
