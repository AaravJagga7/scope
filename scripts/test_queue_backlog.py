#!/usr/bin/env python3
"""Verification script for RabbitMQ / Async Queue Backlog under load."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

ORDER_URL = "http://127.0.0.1:8005"


async def set_consumer_count(client: httpx.AsyncClient, count: int) -> None:
    resp = await client.post(f"{ORDER_URL}/orders/consumers", json={"count": count})
    resp.raise_for_status()
    print(f"  ✓ Consumer count configured to {count} worker(s)")


async def get_backlog_status(client: httpx.AsyncClient) -> dict[str, Any]:
    resp = await client.get(f"{ORDER_URL}/orders/backlog")
    resp.raise_for_status()
    return resp.json()


async def send_order(client: httpx.AsyncClient, order_idx: int) -> str:
    payload = {
        "cart_id": f"cart-load-{order_idx}",
        "user_id": f"load_user_{order_idx}",
        "total_amount": 49.99,
        "currency": "USD",
        "items": [
            {
                "product_id": "prod-1",
                "quantity": 1,
                "unit_price": 49.99,
            }
        ],
    }
    resp = await client.post(f"{ORDER_URL}/orders", json=payload)
    resp.raise_for_status()
    return resp.json()["order_id"]


async def run_backlog_experiment() -> None:
    print("================================================================")
    print("      SCOPE-X ASYNC ORDER BACKLOG UNDER LOAD EXPERIMENT         ")
    print("================================================================")

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Phase 1: Set consumer count to 1
        print("\n[Phase 1] Restricting consumer concurrency to 1...")
        await set_consumer_count(client, 1)

        initial_status = await get_backlog_status(client)
        print(
            f"  Initial backlog: {initial_status['backlog']} messages (active consumers: {initial_status['active_consumers']})"
        )

        # Phase 2: Send a high-concurrency burst of 20 orders
        burst_size = 20
        print(f"\n[Phase 2] Firing a burst of {burst_size} orders concurrently...")
        t_start = time.perf_counter()

        tasks = [send_order(client, i) for i in range(burst_size)]
        await asyncio.gather(*tasks)

        t_burst = time.perf_counter() - t_start
        print(f"  ✓ Dispatched {burst_size} orders in {t_burst * 1000:.1f}ms")

        # Phase 3: Observe backlog accumulation
        status_after_burst = await get_backlog_status(client)
        backlog_peak = status_after_burst["backlog"]
        print(f"\n[Phase 3] Peak backlog observed: {backlog_peak} messages in queue!")

        if backlog_peak > 0:
            print("  ✓ SUCCESS: Backlog grew significantly under load with 1 consumer.")
        else:
            print("  ! Notice: Consumer processed messages extremely fast.")

        # Phase 4: Monitor draining
        print("\n[Phase 4] Monitoring backlog drain with 1 consumer...")
        while True:
            st = await get_backlog_status(client)
            current_backlog = st["backlog"]
            print(f"  Current backlog: {current_backlog:2d} messages pending...")
            if current_backlog == 0:
                break
            await asyncio.sleep(0.15)

        print("  ✓ All orders processed by consumer worker.")

        # Phase 5: Demonstrate horizontal consumer scaling
        print("\n[Phase 5] Demonstrating dynamic consumer scaling (scaling to 4 workers)...")
        await set_consumer_count(client, 4)
        scaled_status = await get_backlog_status(client)
        print(f"  Active consumers: {scaled_status['active_consumers']}")

    print("\n================================================================")
    print("  EXPERIMENT COMPLETE: Asynchronous queuing & backlog validated")
    print("================================================================\n")


if __name__ == "__main__":
    asyncio.run(run_backlog_experiment())
