import pytest

from experiment_engine.experiment_runner import run_experiment
from flash_sale_shield.config import ShieldConfig
from flash_sale_shield.queue_manager import ShieldCore
from flash_sale_shield.rate_limiter import TokenBucket
from flash_sale_shield.waiting_room import Ticket, WaitingRoom


def test_token_bucket_burst_then_rate():
    b = TokenBucket(rate=10, capacity=5, now=0)
    assert sum(b.try_acquire(0) for _ in range(10)) == 5        # burst = capacity
    assert not b.try_acquire(0.05)                               # 0.5 token
    assert b.try_acquire(0.1)                                    # 1 token after 0.1 s
    assert b.time_until_available(0.1) == pytest.approx(0.1)


def test_token_bucket_never_exceeds_capacity():
    b = TokenBucket(rate=100, capacity=3, now=0)
    assert b.available(1000) == 3


def test_waiting_room_fifo_and_bounded():
    room = WaitingRoom(capacity=3)
    for i in range(5):
        room.offer(Ticket(i, i, i))
    assert len(room) == 3 and room.rejected_full == 2
    assert [room.poll().request_id for _ in range(3)] == [0, 1, 2]


def test_waiting_room_expiry():
    room = WaitingRoom(capacity=10, max_wait_s=2)
    room.offer(Ticket(1, 0, 0))
    room.offer(Ticket(2, 1, 1))
    assert [t.request_id for t in room.expire(2.5)] == [1]
    assert len(room) == 1


def test_core_fifo_no_bypass():
    core = ShieldCore(ShieldConfig(admission_rate=1, bucket_capacity=1, queue_capacity=10))
    assert core.on_arrival(1, 0.0).status == "admitted"
    assert core.on_arrival(2, 0.1).status == "queued"
    # a token is available at t=1.0, but request 3 must not jump ahead of 2
    assert core.on_arrival(3, 1.0).status == "queued"
    admitted, _ = core.dispatch(1.0)
    assert [t.request_id for t in admitted] == [2]


def test_core_rejects_when_full():
    core = ShieldCore(ShieldConfig(admission_rate=1, bucket_capacity=1, queue_capacity=2))
    outs = [core.on_arrival(i, 0.0).status for i in range(5)]
    assert outs == ["admitted", "queued", "queued", "rejected", "rejected"]


def test_admission_inflight_cap_and_completion_unblocks():
    core = ShieldCore(ShieldConfig(admission_rate=1000, bucket_capacity=1000, queue_capacity=10,
                                   max_inflight=2, downstream_limit=100))
    outs = [core.on_arrival(i, 0.0).status for i in range(4)]
    assert outs == ["admitted", "admitted", "queued", "queued"]
    assert core.next_wakeup(0.0) is not None or core.inflight == 2
    core.on_complete(0.01, 10.0)
    admitted, _ = core.dispatch(0.01)
    assert [t.request_id for t in admitted] == [2]


def test_reconfigure_at_runtime():
    core = ShieldCore(ShieldConfig())
    core.reconfigure(0.0, admission_rate=50, queue_capacity=10)
    assert core.bucket.rate == 50 and core.room.capacity == 10
    with pytest.raises(KeyError):
        core.reconfigure(0.0, not_a_param=1)


X = dict(traffic_rate=600, replicas=2, admission_rate=150, queue_capacity=200, bucket_capacity=20)


def test_sim_queue_bounded_and_rate_respected():
    row = run_experiment(X, seed=3)
    assert row["queue_max"] <= X["queue_capacity"]
    # admitted rate cannot exceed a*T + B
    assert row["admitted"] <= X["admission_rate"] * row["sim_time_s"] + X["bucket_capacity"]
    assert row["rejection_rate"] > 0.3


def test_sim_reproducible():
    assert run_experiment(X, seed=5)["L95_ms"] == run_experiment(X, seed=5)["L95_ms"]
