"""Tests for the flash-sale shield and the inventory TTL holds."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from services.inventory.main import (
    InventoryHold,
    InventoryRecord,
    SessionLocal,
    sweep_expired_holds,
)
from services.inventory.main import app as inventory_app
from storefront.drop import DropParams
from storefront.shield import (
    BehaviorScorer,
    ProofOfWork,
    Shield,
    ShieldConfig,
    TokenBucket,
    leading_zero_bits,
    solve_pow,
)

BROWSER = "Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Safari/605.1.15"
HUMAN_SIGNALS = {"p": 120.0, "k": 2.0, "t": 4000.0}

# -- primitives ----------------------------------------------------------------


def test_token_bucket_refills_at_rate():
    b = TokenBucket(rate=2.0, burst=2.0, now=0.0)
    assert b.take(0.0) and b.take(0.0)
    assert not b.take(0.0)
    assert b.wait_time(0.0) == pytest.approx(0.5)
    assert b.take(0.5)


def test_leading_zero_bits():
    assert leading_zero_bits(b"\x00\x0f") == 12
    assert leading_zero_bits(b"\x80") == 0


def test_pow_is_single_use_and_bound_to_session():
    pow_ = ProofOfWork()
    ch = pow_.issue("s1", 10, now=0.0)
    nonce = solve_pow(ch["challenge"], 10)
    assert pow_.verify("s2", ch["challenge"], str(nonce), 1.0)[0] is False  # other session
    ch = pow_.issue("s1", 10, now=0.0)
    nonce = solve_pow(ch["challenge"], 10)
    assert pow_.verify("s1", ch["challenge"], str(nonce), 1.0)[0] is True
    assert pow_.verify("s1", ch["challenge"], str(nonce), 1.0)[0] is False  # reused


def test_pow_rejects_expired_and_wrong_answers():
    pow_ = ProofOfWork(ttl_s=5)
    ch = pow_.issue("s", 16, now=0.0)
    assert pow_.verify("s", ch["challenge"], "0", 100.0)[1].startswith("unknown")
    ch = pow_.issue("s", 20, now=0.0)
    # An answer for a lower difficulty almost surely fails the higher one.
    weak = solve_pow(ch["challenge"], 1)
    ok, _, _ = pow_.verify("s", ch["challenge"], str(weak), 1.0)
    assert ok is (leading_zero_bits(_digest(ch["challenge"], weak)) >= 20)


def _digest(challenge: str, nonce: int) -> bytes:
    import hashlib

    return hashlib.sha256(f"{challenge}:{nonce}".encode()).digest()


def test_scorer_separates_scripted_bot_from_shopper():
    sc = BehaviorScorer()
    sc.observe("human", "10.0.0.1", 0.0, HUMAN_SIGNALS)
    sc.note_view("human", "bz-701", 0.0)
    score, reasons = sc.score("human", "10.0.0.1", BROWSER, "add_to_cart", "bz-701", 3.0)
    assert score == 0 and reasons == []

    for i in range(8):  # cookie-dropping bot: new session every request, same IP
        sc.observe(f"bot{i}", "10.9.0.1", 1.0 + i * 0.01, None)
    score, reasons = sc.score("bot7", "10.9.0.1", "python-httpx/0.27", "add_to_cart", "bz-701", 1.1)
    assert score >= 70
    assert {"automation_user_agent", "no_product_view", "many_sessions_per_ip"} <= set(reasons)


def test_config_validation_and_patch():
    cfg = ShieldConfig().updated({"admit_per_minute": "120", "waiting_room": True, "bogus": 1})
    assert cfg.admit_per_minute == 120 and cfg.waiting_room is True
    with pytest.raises(ValueError):
        ShieldConfig().updated({"challenge_score": 90, "block_score": 50})


# -- shield decisions -----------------------------------------------------------


def _guard(shield: Shield, sid: str, now: float, ua: str = BROWSER, signals=HUMAN_SIGNALS):
    return shield.guard(
        sid=sid,
        ip=f"10.0.0.{hash(sid) % 200}",
        user_agent=ua,
        action="checkout",
        signals=signals,
        now=now,
    )


def test_shield_off_allows_everything():
    d = Shield().guard(sid="x", ip="1.1.1.1", user_agent="", action="add_to_cart", now=0.0)
    assert d.allowed


def test_waiting_room_queues_and_admits_fifo_at_rate():
    shield = Shield(ShieldConfig(waiting_room=True, force_queue=True, admit_per_minute=60))
    for i in range(3):
        d = _guard(shield, f"u{i}", 0.0)
        assert not d.allowed and d.code == "queue_required"
        shield.join(f"u{i}", "10.0.0.1", now=0.0)
    assert shield.queue_status("u2", 0.0)["position"] == 3
    # 1 admission per second; the queue is polled so nobody counts as abandoned.
    shield.queue_status("u0", 1.05)
    assert shield.queue_status("u0", 1.05)["state"] == "admitted"
    assert shield.queue_status("u1", 1.05)["state"] == "queued"
    assert _guard(shield, "u0", 1.1).allowed
    shield.queue_status("u1", 2.1)
    assert shield.queue_status("u1", 2.1)["state"] == "admitted"
    assert shield.queue_status("u2", 2.1)["position"] == 1


def test_waiting_room_opens_on_traffic_threshold_only():
    shield = Shield(ShieldConfig(waiting_room=True, activate_rps=2, cooldown_s=5))
    assert _guard(shield, "early", 0.0).allowed  # quiet store: walk straight in
    shield.room.purchase_hits.extend([0.5] * 20)
    late = _guard(shield, "late", 1.0)
    assert not late.allowed and late.code == "queue_required"
    assert _guard(shield, "early", 1.0).allowed  # already admitted shoppers keep going


def test_bot_defense_blocks_scripted_bot_and_challenges_suspicious():
    shield = Shield(ShieldConfig(bot_defense=True))
    # automation UA 35 + no product view 25 + no interaction signals 15 = 75 >= 70
    d = shield.guard(
        sid="b",
        ip="10.9.0.1",
        user_agent="curl/8",
        action="add_to_cart",
        product_id="bz-701",
        now=0.0,
    )
    assert d.code == "blocked" and d.detail["score"] == 75
    # Browser-looking bot: no product view 25 + no signals 15 = 40 -> challenge, not block.
    d = shield.guard(
        sid="b2",
        ip="10.9.0.2",
        user_agent=BROWSER,
        action="add_to_cart",
        product_id="bz-701",
        now=0.0,
    )
    assert d.code == "challenge_required" and d.detail["bits"] == shield.config.pow_bits + 4


def test_rate_limit_per_device():
    shield = Shield(
        ShieldConfig(
            bot_defense=True, device_rate_per_s=1, device_burst=2, ip_rate_per_s=100, ip_burst=100
        )
    )
    results = [_guard(shield, "same", 0.0).code for _ in range(4)]
    assert results[:2] == ["ok", "ok"] and "rate_limited" in results[2:]


def test_queue_join_requires_pow_with_bot_defense():
    shield = Shield(ShieldConfig(waiting_room=True, force_queue=True, bot_defense=True, pow_bits=8))
    d = shield.join("s", "10.0.0.1", now=0.0)
    assert d.code == "challenge_required"
    nonce = solve_pow(d.detail["challenge"], d.detail["bits"])
    assert shield.solve("s", d.detail["challenge"], str(nonce), now=0.1).allowed
    assert shield.join("s", "10.0.0.1", now=0.2).allowed


def test_drop_params_are_clamped():
    p = DropParams.from_dict({"humans": 10_000, "stock": 0, "arms": "nonsense"})
    assert p.humans == 600 and p.stock == 1 and p.arms == "both"


# -- inventory TTL holds ------------------------------------------------------------


@pytest.fixture()
def inv():
    return TestClient(inventory_app)


def _set_stock(pid: str, qty: int) -> None:
    with SessionLocal() as db:
        db.query(InventoryHold).filter(InventoryHold.product_id == pid).delete()
        rec = db.query(InventoryRecord).filter_by(product_id=pid).first()
        if rec is None:
            db.add(InventoryRecord(product_id=pid, available_qty=qty, reserved_qty=0))
        else:
            rec.available_qty, rec.reserved_qty = qty, 0
        db.commit()


def _stock(inv: TestClient, pid: str) -> dict:
    return inv.get(f"/inventory/{pid}").json()


def test_hold_reserves_and_refuses_when_sold_out(inv):
    _set_stock("hold-a", 2)
    body = {"hold_id": "c1", "product_id": "hold-a", "quantity": 2, "ttl_seconds": 60}
    assert inv.post("/inventory/holds", json=body).status_code == 200
    assert _stock(inv, "hold-a") == {"product_id": "hold-a", "available_qty": 0, "reserved_qty": 2}
    other = dict(body, hold_id="c2", quantity=1)
    assert inv.post("/inventory/holds", json=other).status_code == 409


def test_expired_hold_returns_stock(inv):
    _set_stock("hold-b", 3)
    inv.post(
        "/inventory/holds",
        json={"hold_id": "c1", "product_id": "hold-b", "quantity": 3, "ttl_seconds": 5},
    )
    with SessionLocal() as db:
        assert sweep_expired_holds(db, now=time.time() + 10) >= 3
    assert _stock(inv, "hold-b")["available_qty"] == 3
    view = inv.get("/inventory/holds/c1").json()
    assert (
        next(line for line in view["lines"] if line["product_id"] == "hold-b")["status"]
        == "expired"
    )


def test_lock_commit_and_release(inv):
    _set_stock("hold-c", 5)
    for hid in ("buyer", "leaver"):
        inv.post("/inventory/holds", json={"hold_id": hid, "product_id": "hold-c", "quantity": 1})
    items = {"items": [{"product_id": "hold-c", "quantity": 1}]}
    assert inv.post("/inventory/holds/buyer/lock", json=items).status_code == 200
    assert inv.post("/inventory/holds/buyer/commit", json=items).status_code == 200
    inv.delete("/inventory/holds/leaver")
    assert _stock(inv, "hold-c") == {"product_id": "hold-c", "available_qty": 4, "reserved_qty": 0}
    # Locking a cart with no active hold fails instead of selling unreserved stock.
    assert inv.post("/inventory/holds/leaver/lock", json=items).status_code == 409


def test_per_customer_limit(inv):
    _set_stock("hold-d", 10)
    body = {"hold_id": "c", "product_id": "hold-d", "quantity": 2, "max_per_hold": 2}
    assert inv.post("/inventory/holds", json=body).status_code == 200
    assert inv.post("/inventory/holds", json=dict(body, quantity=1)).status_code == 409


def test_concurrent_holds_never_oversell(inv):
    _set_stock("hold-e", 5)

    def grab(i: int) -> int:
        return inv.post(
            "/inventory/holds",
            json={"hold_id": f"race{i}", "product_id": "hold-e", "quantity": 1},
        ).status_code

    with ThreadPoolExecutor(max_workers=16) as pool:
        codes = list(pool.map(grab, range(40)))
    assert codes.count(200) == 5
    assert _stock(inv, "hold-e") == {"product_id": "hold-e", "available_qty": 0, "reserved_qty": 5}
