"""Locustfile defining synthetic user behaviors and workload profiles for SCOPE-X."""

from __future__ import annotations

import os
import random
import sys
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from locust import HttpUser, between, task

from workload.profiles import PROFILES


def is_mock_mode() -> bool:
    """Dynamically checks if synthetic simulation mode is active."""
    return os.getenv("WORKLOAD_MOCK", "0") in ("1", "true", "True")


class BaseScopeXUser(HttpUser):
    """Base user class implementing standard e-commerce microservice interactions."""

    abstract = True
    cart_id: str = ""
    user_id: str = ""
    rng: random.Random = random.Random()

    def on_start(self) -> None:
        """Initializes user session with random identifier and personal cart."""
        seed = int(os.getenv("WORKLOAD_SEED", "42"))
        self.rng = random.Random(seed + hash(self))
        self.user_id = f"user_{uuid.uuid4().hex[:8]}"
        self.cart_id = f"cart_{uuid.uuid4().hex[:10]}"

    def _get_url(self, target_port: int, path: str) -> str:
        """Computes service URL from Locust base host or environment overrides."""
        parsed = urllib.parse.urlparse(self.host or "http://127.0.0.1:8006")
        netloc = parsed.netloc
        host_name = netloc.split(":")[0] if ":" in netloc else netloc
        scheme = parsed.scheme or "http"
        return f"{scheme}://{host_name}:{target_port}{path}"

    def browse_catalog(self, product_id: str | None = None) -> list[dict[str, Any]]:
        """Browses product catalog or queries a specific product."""
        if is_mock_mode():
            t0 = time.perf_counter()
            time.sleep(self.rng.uniform(0.005, 0.015))
            latency = (time.perf_counter() - t0) * 1000.0
            self.environment.events.request.fire(
                request_type="GET",
                name="/products",
                response_time=latency,
                response_length=512,
                exception=None,
                context={},
            )
            return [{"id": product_id or "prod-1", "name": "Cloud Native Hoodie", "price": 49.99}]

        prod_url = self._get_url(8001, "/products")
        with self.client.get(prod_url, name="/products", timeout=5.0, catch_response=True) as resp:
            if resp.status_code == 200:
                resp.success()
                data = resp.json()
                return data if isinstance(data, list) else data.get("products", [])
            resp.failure(f"Status {resp.status_code}: {resp.text}")
            return []

    def add_to_cart(self, product_id: str = "prod-1", quantity: int = 1) -> bool:
        """Adds product to cart on Cart Service."""
        if is_mock_mode():
            t0 = time.perf_counter()
            time.sleep(self.rng.uniform(0.008, 0.020))
            latency = (time.perf_counter() - t0) * 1000.0
            self.environment.events.request.fire(
                request_type="POST",
                name="/cart/items",
                response_time=latency,
                response_length=256,
                exception=None,
                context={},
            )
            return True

        cart_url = self._get_url(8002, f"/cart/{self.cart_id}/items")
        payload = {
            "product_id": product_id,
            "quantity": quantity,
            "unit_price": 49.99,
        }
        with self.client.post(
            cart_url, json=payload, name="/cart/items", timeout=5.0, catch_response=True
        ) as resp:
            if resp.status_code == 200:
                resp.success()
                return True
            resp.failure(f"Cart add failed ({resp.status_code}): {resp.text}")
            return False

    def execute_checkout(self) -> bool:
        """Executes full checkout orchestration across microservices."""
        # Ensure cart has at least 1 item
        self.add_to_cart("prod-1", 1)

        if is_mock_mode():
            t0 = time.perf_counter()
            # Simulate microservice chain processing latency (20-60ms)
            time.sleep(self.rng.uniform(0.020, 0.060))
            latency = (time.perf_counter() - t0) * 1000.0
            self.environment.events.request.fire(
                request_type="POST",
                name="/checkout",
                response_time=latency,
                response_length=340,
                exception=None,
                context={},
            )
            return True

        checkout_url = (
            "/checkout" if ":8006" in (self.host or "") else self._get_url(8006, "/checkout")
        )
        payload = {
            "cart_id": self.cart_id,
            "user_id": self.user_id,
            "payment_method": "credit_card",
        }
        with self.client.post(
            checkout_url, json=payload, name="/checkout", timeout=8.0, catch_response=True
        ) as resp:
            if resp.status_code == 200:
                resp.success()
                # Renew cart for subsequent checkout
                self.cart_id = f"cart_{uuid.uuid4().hex[:10]}"
                return True
            resp.failure(f"Checkout failed ({resp.status_code}): {resp.text}")
            return False


class NormalUser(BaseScopeXUser):
    """Simulates realistic e-commerce user behavior."""

    cfg = PROFILES["normal"]
    wait_time = between(cfg.min_wait, cfg.max_wait)

    @task(50)
    def task_browse(self) -> None:
        self.browse_catalog()

    @task(30)
    def task_cart(self) -> None:
        p_id = self.rng.choice(["prod-1", "prod-2", "prod-3"])
        self.add_to_cart(p_id, self.rng.randint(1, 3))

    @task(20)
    def task_checkout(self) -> None:
        self.execute_checkout()


class FlashSaleUser(BaseScopeXUser):
    """Simulates sudden surge of users rushing to purchase items immediately."""

    cfg = PROFILES["flash_sale"]
    wait_time = between(cfg.min_wait, cfg.max_wait)

    @task(80)
    def task_spike_checkout(self) -> None:
        self.execute_checkout()

    @task(10)
    def task_browse(self) -> None:
        self.browse_catalog("prod-1")

    @task(10)
    def task_add_cart(self) -> None:
        self.add_to_cart("prod-1", 1)


class RampUser(BaseScopeXUser):
    """Simulates users during a linear ramp experiment."""

    cfg = PROFILES["ramp"]
    wait_time = between(cfg.min_wait, cfg.max_wait)

    @task(40)
    def task_checkout(self) -> None:
        self.execute_checkout()

    @task(35)
    def task_browse(self) -> None:
        self.browse_catalog()

    @task(25)
    def task_cart(self) -> None:
        self.add_to_cart("prod-2", 1)


class ViralProductUser(BaseScopeXUser):
    """Simulates traffic overwhelmingly focused on a single viral product."""

    cfg = PROFILES["viral_product"]
    wait_time = between(cfg.min_wait, cfg.max_wait)

    @task(70)
    def task_viral_checkout(self) -> None:
        self.add_to_cart("prod-1", self.rng.randint(1, 2))
        self.execute_checkout()

    @task(20)
    def task_viral_browse(self) -> None:
        self.browse_catalog("prod-1")

    @task(10)
    def task_viral_cart(self) -> None:
        self.add_to_cart("prod-1", 1)


class SlowPaymentUser(BaseScopeXUser):
    """Simulates checkout load under degraded payment gateway performance."""

    cfg = PROFILES["slow_payment_api"]
    wait_time = between(cfg.min_wait, cfg.max_wait)

    @task(85)
    def task_contended_checkout(self) -> None:
        self.execute_checkout()

    @task(15)
    def task_browse(self) -> None:
        self.browse_catalog()


PROFILE_USER_CLASSES: dict[str, type[BaseScopeXUser]] = {
    "normal": NormalUser,
    "flash_sale": FlashSaleUser,
    "ramp": RampUser,
    "viral_product": ViralProductUser,
    "slow_payment_api": SlowPaymentUser,
}
