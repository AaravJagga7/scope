"""Unit and integration tests for asynchronous order queuing and backlog metrics."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from services.common.messaging import QUEUE_NAME, MessageBroker
from services.common.metrics import SERVICE_QUEUE_LENGTH
from services.order.main import app as order_app


@pytest.fixture
def order_client() -> TestClient:
    return TestClient(order_app)


def test_order_backlog_metric_exposed(order_client: TestClient) -> None:
    """Verifies that service_queue_length metric is exposed in Prometheus output."""
    SERVICE_QUEUE_LENGTH.labels(service="order-service", queue_name=QUEUE_NAME).set(7)
    resp = order_client.get("/metrics")
    assert resp.status_code == 200
    assert "service_queue_length" in resp.text
    assert 'queue_name="orders_queue"' in resp.text


def test_consumer_concurrency_config(order_client: TestClient) -> None:
    """Verifies consumer count can be dynamically updated via API."""
    resp = order_client.post("/orders/consumers", json={"count": 3})
    assert resp.status_code == 200
    assert resp.json()["active_consumers"] == 3

    # Reset to 1
    resp_reset = order_client.post("/orders/consumers", json={"count": 1})
    assert resp_reset.status_code == 200
    assert resp_reset.json()["active_consumers"] == 1


def test_async_order_creation_flow(order_client: TestClient) -> None:
    """Verifies that POST /orders returns 201 immediately with PENDING status and queues message."""
    payload = {
        "cart_id": "cart_async_test",
        "user_id": "test_user",
        "total_amount": 99.98,
        "currency": "USD",
        "items": [
            {
                "product_id": "prod-1",
                "quantity": 2,
                "unit_price": 49.99,
            }
        ],
    }
    resp = order_client.post("/orders", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["order_id"].startswith("ord_")
    assert data["status"] == "PENDING"


@pytest.mark.asyncio
async def test_backlog_grows_with_single_consumer() -> None:
    """Directly verifies broker queue backlog growth when production exceeds 1 slow consumer."""
    # Create a fresh broker to avoid event-loop issues with the singleton
    broker = MessageBroker(rabbitmq_url="amqp://guest:guest@localhost:5672/")
    await broker.connect()  # will fall back to in-memory

    processed: list[dict] = []

    async def slow_handler(data: dict) -> None:
        await asyncio.sleep(0.05)
        processed.append(data)

    # 1 Consumer
    await broker.set_consumer_concurrency(1, slow_handler)

    # Publish 10 messages quickly
    for i in range(10):
        await broker.publish(QUEUE_NAME, {"idx": i, "order_id": f"ord_burst_{i}"})

    # Sample backlog immediately before slow consumer finishes
    backlog = await broker.get_backlog(QUEUE_NAME)
    assert backlog > 0, "Backlog must grow under load when consumer count is 1"

    # Wait for consumer to drain
    for _ in range(60):
        if len(processed) >= 10:
            break
        await asyncio.sleep(0.05)

    assert len(processed) == 10
    final_backlog = await broker.get_backlog(QUEUE_NAME)
    assert final_backlog == 0

    # Cleanup
    await broker.stop()
