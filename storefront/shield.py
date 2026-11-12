"""Flash-sale shield: edge bot defence + virtual waiting room for the purchase path.

The shield sits in front of the purchase path (add-to-cart and checkout). Browsing
the catalog stays open because it is served from memory and costs almost nothing;
the scarce resources during a drop are checkout capacity and stock.

Three layers, each switchable so their effect can be measured separately:

* **Bot defence** - per-IP and per-device token-bucket rate limits, a behaviour
  score built from explainable signals, and a SHA-256 proof-of-work challenge
  for suspicious sessions and for joining the queue. There is no CAPTCHA
  provider locally; proof-of-work is the challenge mechanism.
* **Waiting room** - turns on when purchase-path traffic or the number of active
  shoppers exceeds a threshold (or when forced for a scheduled drop). New
  visitors get a FIFO queue position and are admitted at a fixed rate.
* **Stock reservation** - handled by the inventory service (TTL holds); the
  shield only carries its settings (TTL, per-customer limit).

Thresholds are configurable defaults to be validated experimentally (see the
drop test in ``storefront/drop.py``), not values assumed to be right.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ShieldConfig:
    """Settings the Constraint Lab can change at runtime (no service restart)."""

    # waiting room
    waiting_room: bool = False
    force_queue: bool = False  # "drop mode": queue every new shopper regardless of load
    activate_rps: float = 40.0  # purchase-path requests/s (5 s window) that open the room
    max_active_shoppers: int = 150
    admit_per_minute: int = 500
    shopper_idle_s: float = 60.0  # admitted shopper counts as active this long after last request
    pass_ttl_s: float = 600.0
    cooldown_s: float = 10.0  # room stays open this long after load falls (hysteresis)
    # stock reservation (enforced by the inventory service)
    reservations: bool = False
    hold_ttl_s: float = 300.0
    max_per_customer: int = 2  # only for limited items
    # bot defence
    bot_defense: bool = False
    ip_rate_per_s: float = 8.0
    ip_burst: float = 20.0
    device_rate_per_s: float = 4.0
    device_burst: float = 10.0
    pow_bits: int = 14  # base proof-of-work difficulty (leading zero bits)
    challenge_score: int = 40
    block_score: int = 70
    trust_forwarded_for: bool = True  # only honoured from loopback peers

    def validate(self) -> None:
        checks = [
            (1 <= self.activate_rps <= 10_000, "activate_rps must be 1..10000"),
            (1 <= self.max_active_shoppers <= 100_000, "max_active_shoppers must be 1..100000"),
            (1 <= self.admit_per_minute <= 100_000, "admit_per_minute must be 1..100000"),
            (5 <= self.hold_ttl_s <= 3600, "hold_ttl_s must be 5..3600"),
            (1 <= self.max_per_customer <= 20, "max_per_customer must be 1..20"),
            (0 < self.ip_rate_per_s <= 1000, "ip_rate_per_s must be in (0, 1000]"),
            (0 < self.device_rate_per_s <= 1000, "device_rate_per_s must be in (0, 1000]"),
            (8 <= self.pow_bits <= 22, "pow_bits must be 8..22"),
            (
                0 <= self.challenge_score <= self.block_score <= 100,
                "need 0 <= challenge <= block <= 100",
            ),
        ]
        for ok, msg in checks:
            if not ok:
                raise ValueError(msg)

    def updated(self, patch: dict[str, Any]) -> ShieldConfig:
        data = asdict(self)
        for key, value in patch.items():
            if key not in data:
                continue
            kind = type(data[key])
            data[key] = value if kind is bool and isinstance(value, bool) else kind(value)
        cfg = ShieldConfig(**data)
        cfg.validate()
        return cfg


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------


class TokenBucket:
    """Classic token bucket: ``rate`` tokens/s refill, at most ``burst`` stored."""

    __slots__ = ("rate", "burst", "tokens", "stamp")

    def __init__(self, rate: float, burst: float, now: float) -> None:
        self.rate, self.burst = rate, burst
        self.tokens, self.stamp = burst, now

    def refill(self, now: float) -> None:
        self.tokens = min(self.burst, self.tokens + (now - self.stamp) * self.rate)
        self.stamp = now

    def take(self, now: float, n: float = 1.0) -> bool:
        self.refill(now)
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False

    def wait_time(self, now: float, n: float = 1.0) -> float:
        self.refill(now)
        return max(0.0, (n - self.tokens) / self.rate)


class RateLimiter:
    """Token buckets keyed by (scope, key), bounded in memory (LRU eviction)."""

    def __init__(self, max_keys: int = 50_000) -> None:
        self.buckets: OrderedDict[tuple[str, str], TokenBucket] = OrderedDict()
        self.max_keys = max_keys

    def check(self, scope: str, key: str, rate: float, burst: float, now: float) -> float:
        """Returns 0.0 if allowed, else seconds until a token is available."""
        bucket = self.buckets.get((scope, key))
        if bucket is None or bucket.rate != rate or bucket.burst != burst:
            bucket = TokenBucket(rate, burst, now)
            self.buckets[(scope, key)] = bucket
        self.buckets.move_to_end((scope, key))
        while len(self.buckets) > self.max_keys:
            self.buckets.popitem(last=False)
        return 0.0 if bucket.take(now) else round(bucket.wait_time(now), 2)


def leading_zero_bits(digest: bytes) -> int:
    bits = 0
    for byte in digest:
        if byte == 0:
            bits += 8
            continue
        bits += 8 - byte.bit_length()
        break
    return bits


def solve_pow(challenge: str, bits: int, max_iter: int = 50_000_000) -> int:
    """Reference solver (used by tests and the simulated clients)."""
    prefix = challenge.encode() + b":"
    for nonce in range(max_iter):
        if leading_zero_bits(hashlib.sha256(prefix + str(nonce).encode()).digest()) >= bits:
            return nonce
    raise RuntimeError("no proof-of-work solution found")


class ProofOfWork:
    """Issues single-use challenges bound to a session; verifies SHA-256 solutions."""

    def __init__(self, ttl_s: float = 120.0) -> None:
        self.ttl = ttl_s
        self.issued: OrderedDict[str, tuple[str, int, float]] = OrderedDict()

    def issue(self, sid: str, bits: int, now: float) -> dict[str, Any]:
        self._expire(now)
        challenge = secrets.token_hex(12)
        self.issued[challenge] = (sid, bits, now + self.ttl)
        return {"challenge": challenge, "bits": bits, "expires_in_s": self.ttl}

    def verify(self, sid: str, challenge: str, nonce: str, now: float) -> tuple[bool, str, int]:
        self._expire(now)
        entry = self.issued.pop(challenge, None)  # single use, even when the answer is wrong
        if entry is None:
            return False, "unknown, expired or reused challenge", 0
        owner, bits, _ = entry
        if owner != sid:
            return False, "challenge was issued to another session", 0
        digest = hashlib.sha256(f"{challenge}:{nonce}".encode()).digest()
        if leading_zero_bits(digest) < bits:
            return False, "solution does not meet the difficulty", 0
        return True, "ok", bits

    def _expire(self, now: float) -> None:
        while self.issued:
            key, (_, _, exp) = next(iter(self.issued.items()))
            if exp >= now and len(self.issued) < 100_000:
                break
            self.issued.popitem(last=False)


# ---------------------------------------------------------------------------
# behaviour scoring
# ---------------------------------------------------------------------------

AUTOMATION_UA = re.compile(
    r"curl|wget|python|httpx|aiohttp|requests|go-http|okhttp|java/|scrapy|headless|phantom|selenium|puppeteer|playwright|bot\b|spider",
    re.I,
)


@dataclass
class SessionProfile:
    first_seen: float
    product_views: dict[str, float] = field(default_factory=dict)
    requests: deque = field(default_factory=lambda: deque(maxlen=64))
    pow_bits: int = 0  # highest difficulty this session has solved
    pow_failures: int = 0
    signals: dict[str, float] = field(default_factory=dict)
    last_seen: float = 0.0


class BehaviorScorer:
    """0-100 bot score from explainable signals; every point comes with a reason.

    Heuristic by design: real people with privacy tools or scripts can trip some
    signals, which is why a mid score triggers a challenge, not a block.
    """

    WEIGHTS = {
        "automation_user_agent": 35,
        "missing_user_agent": 25,
        "no_product_view": 25,
        "too_fast_after_view": 20,
        "high_request_rate": 20,
        "many_sessions_per_ip": 25,
        "no_interaction_signals": 15,
        "failed_challenges": 20,
    }

    def __init__(self) -> None:
        self.sessions: OrderedDict[str, SessionProfile] = OrderedDict()
        self.ip_sessions: dict[str, OrderedDict[str, float]] = {}

    def profile(self, sid: str, now: float) -> SessionProfile:
        prof = self.sessions.get(sid)
        if prof is None:
            prof = SessionProfile(first_seen=now)
            self.sessions[sid] = prof
            while len(self.sessions) > 100_000:
                self.sessions.popitem(last=False)
        prof.last_seen = now
        return prof

    def observe(
        self, sid: str, ip: str, now: float, signals: dict[str, float] | None
    ) -> SessionProfile:
        prof = self.profile(sid, now)
        prof.requests.append(now)
        if signals:
            prof.signals = signals
        seen = self.ip_sessions.setdefault(ip, OrderedDict())
        seen[sid] = now
        seen.move_to_end(sid)
        while seen and next(iter(seen.values())) < now - 60:
            seen.popitem(last=False)
        if len(self.ip_sessions) > 50_000:
            self.ip_sessions.pop(next(iter(self.ip_sessions)))
        return prof

    def note_view(self, sid: str, product_id: str, now: float) -> None:
        self.profile(sid, now).product_views[product_id] = now

    def score(
        self,
        sid: str,
        ip: str,
        user_agent: str,
        action: str,
        product_id: str | None,
        now: float,
    ) -> tuple[int, list[str]]:
        prof = self.profile(sid, now)
        reasons: list[str] = []
        if not user_agent.strip():
            reasons.append("missing_user_agent")
        elif AUTOMATION_UA.search(user_agent):
            reasons.append("automation_user_agent")
        if action == "add_to_cart" and product_id:
            viewed = prof.product_views.get(product_id)
            if viewed is None:
                reasons.append("no_product_view")
            elif now - viewed < 0.7:
                reasons.append("too_fast_after_view")
        recent = [t for t in prof.requests if t >= now - 5]
        if len(recent) / 5.0 > 3.0:
            reasons.append("high_request_rate")
        if len(self.ip_sessions.get(ip, {})) > 5:
            reasons.append("many_sessions_per_ip")
        sig = prof.signals
        if not sig or (sig.get("p", 0) + sig.get("k", 0) + sig.get("t", 0)) <= 0:
            reasons.append("no_interaction_signals")
        if prof.pow_failures >= 2:
            reasons.append("failed_challenges")
        score = min(100, sum(self.WEIGHTS[r] for r in reasons))
        return score, reasons


# ---------------------------------------------------------------------------
# waiting room
# ---------------------------------------------------------------------------


class WaitingRoom:
    """FIFO queue with token-bucket admission and load-based activation."""

    def __init__(self) -> None:
        self.queue: OrderedDict[str, dict[str, float]] = OrderedDict()
        self.admitted: dict[str, dict[str, float]] = {}
        self.purchase_hits: deque[float] = deque(maxlen=200_000)
        self.bucket: TokenBucket | None = None
        self.active_since: float | None = None
        self.calm_since: float | None = None
        self.admitted_total = 0
        self.abandoned_total = 0
        self.max_queue = 0
        self.waits: deque[float] = deque(maxlen=5000)

    def reset(self) -> None:
        self.__init__()

    def purchase_rps(self, now: float) -> float:
        while self.purchase_hits and self.purchase_hits[0] < now - 5:
            self.purchase_hits.popleft()
        return len(self.purchase_hits) / 5.0

    def active_shoppers(self, cfg: ShieldConfig, now: float) -> int:
        stale = [
            s
            for s, a in self.admitted.items()
            if a["expires"] < now or a["last_seen"] < now - cfg.shopper_idle_s
        ]
        for s in stale:
            self.admitted.pop(s, None)
        return len(self.admitted)

    def is_open(self, cfg: ShieldConfig, now: float) -> bool:
        """Whether new shoppers must queue right now (with hysteresis)."""
        if not cfg.waiting_room:
            self.active_since = None
            return False
        if cfg.force_queue:
            self.active_since = self.active_since or now
            return True
        hot = (
            self.purchase_rps(now) >= cfg.activate_rps
            or self.active_shoppers(cfg, now) >= cfg.max_active_shoppers
            or bool(self.queue)
        )
        if hot:
            self.calm_since = None
            self.active_since = self.active_since or now
            return True
        if self.active_since is None:
            return False
        self.calm_since = self.calm_since or now
        if now - self.calm_since >= cfg.cooldown_s:
            self.active_since = None
            self.calm_since = None
            return False
        return True

    def admit(self, sid: str, cfg: ShieldConfig, now: float) -> None:
        self.admitted[sid] = {"since": now, "last_seen": now, "expires": now + cfg.pass_ttl_s}
        self.admitted_total += 1

    def release(self, sid: str) -> None:
        """Frees the shopper's slot (e.g. after a completed purchase)."""
        self.admitted.pop(sid, None)

    def has_pass(self, sid: str, now: float) -> bool:
        entry = self.admitted.get(sid)
        if entry is None or entry["expires"] < now:
            return False
        entry["last_seen"] = now
        return True

    def join(self, sid: str, now: float) -> None:
        if sid not in self.queue and sid not in self.admitted:
            self.queue[sid] = {"joined": now, "last_poll": now}
            self.max_queue = max(self.max_queue, len(self.queue))

    def tick(self, cfg: ShieldConfig, now: float) -> None:
        """Admits from the head of the queue at ``admit_per_minute``."""
        rate = cfg.admit_per_minute / 60.0
        if self.bucket is None or self.bucket.rate != rate:
            self.bucket = TokenBucket(rate, max(1.0, rate), now)
            self.bucket.tokens = 0.0
        self.bucket.refill(now)
        while self.queue and self.bucket.tokens >= 1.0:
            if self.active_shoppers(cfg, now) >= cfg.max_active_shoppers:
                break
            sid, entry = self.queue.popitem(last=False)
            if entry["last_poll"] < now - 30:  # left the page; skip without spending a slot
                self.abandoned_total += 1
                continue
            self.bucket.tokens -= 1.0
            self.waits.append(now - entry["joined"])
            self.admit(sid, cfg, now)

    def position(self, sid: str) -> int | None:
        for idx, key in enumerate(self.queue):
            if key == sid:
                return idx + 1
        return None


# ---------------------------------------------------------------------------
# facade
# ---------------------------------------------------------------------------


@dataclass
class Decision:
    allowed: bool
    status: int = 200
    code: str = "ok"
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    retry_after: float | None = None

    def body(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.detail}


class Shield:
    """Combines the layers and keeps counters + a per-second timeline for the Lab."""

    def __init__(self, config: ShieldConfig | None = None) -> None:
        self.config = config or ShieldConfig()
        self.limiter = RateLimiter()
        self.pow = ProofOfWork()
        self.scorer = BehaviorScorer()
        self.room = WaitingRoom()
        self.counters: dict[str, int] = {}
        self.timeline: deque[dict[str, Any]] = deque(maxlen=600)
        self._tl_second: int | None = None
        self._tl_counts: dict[str, int] = {}

    # -- config --------------------------------------------------------------

    def configure(self, patch: dict[str, Any]) -> ShieldConfig:
        self.config = self.config.updated(patch)
        return self.config

    def reset_state(self) -> None:
        """Clears queue, passes, profiles and counters (keeps the config)."""
        self.limiter = RateLimiter()
        self.pow = ProofOfWork()
        self.scorer = BehaviorScorer()
        self.room.reset()
        self.counters.clear()
        self.timeline.clear()
        self._tl_second = None
        self._tl_counts = {}

    # -- bookkeeping ---------------------------------------------------------

    def _count(self, key: str, now: float) -> None:
        self.counters[key] = self.counters.get(key, 0) + 1
        self._roll(now)
        self._tl_counts[key] = self._tl_counts.get(key, 0) + 1

    def _roll(self, now: float) -> None:
        sec = int(now)
        if self._tl_second is None:
            self._tl_second = sec
        while self._tl_second < sec:
            cfg = self.config
            self.timeline.append(
                {
                    "ts": self._tl_second,
                    "queue": len(self.room.queue),
                    "active_shoppers": len(self.room.admitted),
                    "room_open": self.room.active_since is not None,
                    "purchase_rps": round(self.room.purchase_rps(now), 1),
                    "admit_per_minute": cfg.admit_per_minute,
                    **self._tl_counts,
                }
            )
            self._tl_counts = {}
            self._tl_second += 1

    @staticmethod
    def parse_signals(raw: str | None) -> dict[str, float] | None:
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return {k: float(data.get(k, 0)) for k in ("p", "k", "t")}
        except (ValueError, TypeError, AttributeError):
            return None

    def client_ip(self, peer: str, forwarded_for: str | None) -> str:
        if (
            self.config.trust_forwarded_for
            and forwarded_for
            and peer in ("127.0.0.1", "::1", "testclient")
        ):
            return forwarded_for.split(",")[0].strip() or peer
        return peer

    # -- hooks ---------------------------------------------------------------

    def note_view(self, sid: str, product_id: str, now: float | None = None) -> None:
        self.scorer.note_view(sid, product_id, time.time() if now is None else now)

    def guard(
        self,
        *,
        sid: str,
        ip: str,
        user_agent: str,
        action: str,
        product_id: str | None = None,
        signals: dict[str, float] | None = None,
        now: float | None = None,
    ) -> Decision:
        """Decides whether a purchase-path request may reach the store."""
        now = time.time() if now is None else now
        cfg = self.config
        self.room.purchase_hits.append(now)
        self._count("requests", now)

        if cfg.bot_defense:
            prof = self.scorer.observe(sid, ip, now, signals)
            for scope, key, rate, burst in (
                ("ip", ip, cfg.ip_rate_per_s, cfg.ip_burst),
                ("device", sid, cfg.device_rate_per_s, cfg.device_burst),
            ):
                wait = self.limiter.check(scope, key, rate, burst, now)
                if wait > 0:
                    self._count(f"rate_limited_{scope}", now)
                    return Decision(
                        False,
                        429,
                        "rate_limited",
                        f"Too many requests from this {'network' if scope == 'ip' else 'device'}. "
                        f"Try again in {wait:.1f}s.",
                        {"scope": scope},
                        retry_after=wait,
                    )
            score, reasons = self.scorer.score(sid, ip, user_agent, action, product_id, now)
            if score >= cfg.block_score:
                self._count("blocked", now)
                return Decision(
                    False,
                    403,
                    "blocked",
                    "This request looks automated and was blocked at the edge.",
                    {"score": score, "reasons": reasons},
                )
            if score >= cfg.challenge_score and prof.pow_bits < cfg.pow_bits + 4:
                self._count("challenged", now)
                return Decision(
                    False,
                    403,
                    "challenge_required",
                    "Please complete a quick verification.",
                    {
                        "score": score,
                        "reasons": reasons,
                        **self.pow.issue(sid, cfg.pow_bits + 4, now),
                    },
                )

        if cfg.waiting_room:
            self.room.tick(cfg, now)
            if self.room.has_pass(sid, now):
                return Decision(True)
            if self.room.is_open(cfg, now):
                self._count("queue_required", now)
                return Decision(
                    False,
                    403,
                    "queue_required",
                    "High demand: please join the waiting room.",
                    {"pow_required": cfg.bot_defense},
                )
            self.room.admit(sid, cfg, now)  # room closed: walk straight in
        return Decision(True)

    def challenge(self, sid: str, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        return self.pow.issue(sid, self.config.pow_bits, now)

    def solve(self, sid: str, challenge: str, nonce: str, now: float | None = None) -> Decision:
        now = time.time() if now is None else now
        ok, reason, bits = self.pow.verify(sid, challenge, nonce, now)
        prof = self.scorer.profile(sid, now)
        if not ok:
            prof.pow_failures += 1
            self._count("pow_failed", now)
            return Decision(False, 403, "challenge_failed", reason)
        prof.pow_bits = max(prof.pow_bits, bits)
        self._count("pow_solved", now)
        return Decision(True, detail={"verified_bits": prof.pow_bits})

    def join(self, sid: str, ip: str, now: float | None = None) -> Decision:
        now = time.time() if now is None else now
        cfg = self.config
        if cfg.bot_defense:
            wait = self.limiter.check("join", ip, cfg.ip_rate_per_s, cfg.ip_burst, now)
            if wait > 0:
                self._count("rate_limited_ip", now)
                return Decision(
                    False, 429, "rate_limited", "Too many queue joins.", retry_after=wait
                )
            if self.scorer.profile(sid, now).pow_bits < cfg.pow_bits:
                return Decision(
                    False,
                    403,
                    "challenge_required",
                    "Solve the proof-of-work to get a queue position.",
                    self.pow.issue(sid, cfg.pow_bits, now),
                )
        if not self.room.has_pass(sid, now):
            self.room.join(sid, now)
            self._count("joined_queue", now)
        return Decision(True, detail=self.queue_status(sid, now))

    def queue_status(self, sid: str, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        cfg = self.config
        self.room.tick(cfg, now)
        entry = self.room.queue.get(sid)
        if entry is not None:
            entry["last_poll"] = now
        if self.room.has_pass(sid, now):
            state, pos = "admitted", None
        elif entry is not None:
            state, pos = "queued", self.room.position(sid)
        else:
            state, pos = "not_queued", None
        rate = cfg.admit_per_minute / 60.0
        return {
            "state": state,
            "position": pos,
            "ahead": (pos - 1) if pos else None,
            "queue_length": len(self.room.queue),
            "eta_s": round(pos / rate, 1) if pos else None,
            "admit_per_minute": cfg.admit_per_minute,
            "room_open": self.room.is_open(cfg, now),
        }

    def status(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        cfg = self.config
        self.room.tick(cfg, now)
        self._roll(now)
        waits = sorted(self.room.waits)
        p95_wait = waits[int(0.95 * (len(waits) - 1))] if waits else None
        return {
            "config": asdict(cfg),
            "room_open": self.room.is_open(cfg, now),
            "queue_length": len(self.room.queue),
            "max_queue": self.room.max_queue,
            "active_shoppers": self.room.active_shoppers(cfg, now),
            "purchase_rps": round(self.room.purchase_rps(now), 1),
            "admitted_total": self.room.admitted_total,
            "abandoned_in_queue": self.room.abandoned_total,
            "wait_p95_s": round(p95_wait, 1) if p95_wait is not None else None,
            "counters": dict(self.counters),
            "timeline": list(self.timeline)[-180:],
        }
