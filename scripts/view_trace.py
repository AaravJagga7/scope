#!/usr/bin/env python3
"""Inspect OpenTelemetry distributed traces and Prometheus metrics across SCOPE-X services."""

from __future__ import annotations

import sys
import time
from typing import Any

import httpx

CHECKOUT_URL = "http://127.0.0.1:8006"
CART_URL = "http://127.0.0.1:8002"
INVENTORY_URL = "http://127.0.0.1:8003"


def execute_sample_checkout() -> dict[str, Any]:
    cart_id = f"trace-cart-{int(time.time())}"
    print(f"Creating cart {cart_id} and initiating checkout...")
    with httpx.Client(timeout=10.0) as client:
        # 1. Add item to cart
        client.post(
            f"{CART_URL}/cart/{cart_id}/items",
            json={
                "product_id": "prod-1",
                "product_name": "Cloud Native Hoodie",
                "quantity": 1,
                "unit_price": 49.99,
            },
        ).raise_for_status()

        # 2. Trigger checkout
        resp = client.post(
            f"{CHECKOUT_URL}/checkout",
            json={
                "cart_id": cart_id,
                "user_id": "trace_analyst_01",
                "payment_method": "credit_card",
            },
        )
        resp.raise_for_status()
        return resp.json()


def display_trace(trace_id: str | None = None) -> None:
    endpoint = f"{CHECKOUT_URL}/traces/{trace_id}" if trace_id else f"{CHECKOUT_URL}/traces/latest"
    with httpx.Client(timeout=5.0) as client:
        resp = client.get(endpoint)
        if resp.status_code != 200:
            print(f"Could not retrieve trace: {resp.text}")
            return
        data = resp.json()
        print("\n=======================================================")
        print("          SCOPE-X OPENTELEMETRY TRACE VIEW             ")
        print("=======================================================")
        print(f"Trace ID: {data.get('trace_id')}")
        print("\nDistributed Span Hierarchy:")
        print(data.get("formatted_tree", "No tree available"))
        print("\nDetailed Span Summary:")
        for span in data.get("spans", []):
            parent = f"parent={span['parent_id']}" if span["parent_id"] else "ROOT"
            print(
                f"  • [{span['service']:18}] {span['name']:22} {span['duration_ms']:6.2f}ms  ({parent})"
            )
        print("=======================================================\n")


def display_metrics_sample() -> None:
    print("Prometheus Metrics Sample (Inventory DB Pool & Checkout Latencies):")
    with httpx.Client(timeout=5.0) as client:
        inv_metrics = client.get(f"{INVENTORY_URL}/metrics").text
        chk_metrics = client.get(f"{CHECKOUT_URL}/metrics").text

        for line in inv_metrics.splitlines():
            if line.startswith("db_pool_connections") or line.startswith("http_requests_in_flight"):
                print(f"  [Inventory] {line}")

        for line in chk_metrics.splitlines():
            if line.startswith("http_request_duration_seconds_bucket") and 'le="0.5"' in line:
                print(f"  [Checkout]  {line}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] != "--generate":
        target_trace_id = sys.argv[1]
        display_trace(target_trace_id)
    else:
        result = execute_sample_checkout()
        trace_id = result.get("trace_id")
        display_trace(trace_id)
        display_metrics_sample()
