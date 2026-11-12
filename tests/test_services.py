"""Unit and integration tests for SCOPE-X microservices."""

import pytest
from fastapi.testclient import TestClient

from services.cart.main import app as cart_app
from services.checkout.main import app as checkout_app
from services.inventory.main import app as inventory_app
from services.order.main import app as order_app
from services.payment.main import app as payment_app
from services.product.main import app as product_app


@pytest.fixture
def product_client() -> TestClient:
    return TestClient(product_app)


@pytest.fixture
def cart_client() -> TestClient:
    return TestClient(cart_app)


@pytest.fixture
def inventory_client() -> TestClient:
    return TestClient(inventory_app)


@pytest.fixture
def payment_client() -> TestClient:
    return TestClient(payment_app)


@pytest.fixture
def order_client() -> TestClient:
    return TestClient(order_app)


@pytest.fixture
def checkout_client() -> TestClient:
    return TestClient(checkout_app)


# 1. Health and Metrics Endpoint Tests
@pytest.mark.parametrize(
    "app_instance, expected_service",
    [
        (product_app, "product-service"),
        (cart_app, "cart-service"),
        (inventory_app, "inventory-service"),
        (payment_app, "payment-service"),
        (order_app, "order-service"),
        (checkout_app, "checkout-service"),
    ],
)
def test_service_health_and_metrics(app_instance, expected_service: str) -> None:
    client = TestClient(app_instance)

    # Health check
    health_resp = client.get("/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["status"] == "ok"
    assert health_resp.json()["service"] == expected_service

    # Metrics check
    metrics_resp = client.get("/metrics")
    assert metrics_resp.status_code == 200
    assert (
        "http_requests_total" in metrics_resp.text
        or "python_gc_objects_collected_total" in metrics_resp.text
    )


# 2. Product Service Tests
def test_product_service_catalog(product_client: TestClient) -> None:
    resp = product_client.get("/products")
    assert resp.status_code == 200
    products = resp.json()
    assert len(products) >= 3

    # Fetch individual product
    first_id = products[0]["id"]
    single_resp = product_client.get(f"/products/{first_id}")
    assert single_resp.status_code == 200
    assert single_resp.json()["id"] == first_id

    # 404 on missing
    missing_resp = product_client.get("/products/non_existent_id")
    assert missing_resp.status_code == 404


# 3. Cart Service Tests
def test_cart_service_flow(cart_client: TestClient) -> None:
    cart_id = "test_cart_1"

    # Empty cart validation returns 400
    val_resp = cart_client.post(f"/cart/{cart_id}/validate")
    assert val_resp.status_code == 400

    # Add item
    add_resp = cart_client.post(
        f"/cart/{cart_id}/items",
        json={
            "product_id": "prod-1",
            "product_name": "Test Item",
            "quantity": 2,
            "unit_price": 50.0,
        },
    )
    assert add_resp.status_code == 200
    cart = add_resp.json()
    assert len(cart["items"]) == 1
    assert cart["total_amount"] == 100.0

    # Validate populated cart
    val_resp2 = cart_client.post(f"/cart/{cart_id}/validate")
    assert val_resp2.status_code == 200

    # Clear cart
    del_resp = cart_client.delete(f"/cart/{cart_id}")
    assert del_resp.status_code == 200


# 4. Inventory Service Tests
def test_inventory_reservation(inventory_client: TestClient) -> None:
    # Insufficient stock throws 409
    resp = inventory_client.post(
        "/inventory/reserve",
        json={"items": [{"product_id": "prod-1", "quantity": 999999}]},
    )
    assert resp.status_code == 409

    # Valid reservation
    valid_resp = inventory_client.post(
        "/inventory/reserve",
        json={"items": [{"product_id": "prod-1", "quantity": 1}]},
    )
    assert valid_resp.status_code == 200
    assert valid_resp.json()["success"] is True


# 5. Payment Service Tests
def test_payment_processing(payment_client: TestClient) -> None:
    resp = payment_client.post(
        "/payment/process",
        json={"cart_id": "cart_test", "amount": 99.99, "currency": "USD"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "succeeded"
    assert data["transaction_id"].startswith("txn_")


# 6. Order Service Tests (async queue — returns PENDING immediately)
def test_order_creation_returns_pending(order_client: TestClient) -> None:
    order_payload = {
        "cart_id": "cart_order_test",
        "user_id": "user_42",
        "total_amount": 150.0,
        "currency": "USD",
        "items": [{"product_id": "prod-1", "quantity": 3, "unit_price": 50.0}],
    }
    resp = order_client.post("/orders", json=order_payload)
    assert resp.status_code == 201
    order_data = resp.json()
    assert order_data["status"] == "PENDING"
    assert order_data["order_id"].startswith("ord_")
