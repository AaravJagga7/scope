"""Tests for the Bazaaro storefront and its Constraint Lab (no live services needed)."""

from __future__ import annotations

import os

import pytest

os.environ["STOREFRONT_AUTOSTART"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from storefront.app import app  # noqa: E402
from storefront.catalog import CATEGORIES, PRODUCTS, inventory_seed, search  # noqa: E402
from storefront.lab import (  # noqa: E402
    SLO,
    LoadRunner,
    LoadTest,
    WhatIfConfig,
    load_constraint_registry,
    what_if,
)
from storefront.testbed import TestbedConfig  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# -- catalog -------------------------------------------------------------


def test_catalog_ids_unique_and_categories_known():
    ids = [p.id for p in PRODUCTS]
    assert len(ids) == len(set(ids))
    assert {p.category for p in PRODUCTS} <= set(CATEGORIES)
    assert inventory_seed() == {p.id: p.stock for p in PRODUCTS}


def test_search_filters_and_sorts():
    assert all(p.category == "books" for p in search(category="books"))
    cheap = search(max_price=30)
    assert cheap and all(p.price <= 30 for p in cheap)
    prices = [p.price for p in search(sort="price_asc")]
    assert prices == sorted(prices)
    assert all(p.fast_delivery for p in search(fast_only=True))
    assert any("Headphones" in p.name for p in search(query="headphones"))


def test_discount_pct():
    deal = next(p for p in PRODUCTS if p.list_price > p.price)
    assert 0 < deal.discount_pct < 100
    full = next(p for p in PRODUCTS if p.list_price == p.price)
    assert full.discount_pct == 0


# -- API -------------------------------------------------------------------


def test_products_api(client):
    resp = client.get("/api/products", params={"category": "toys", "sort": "rating"})
    assert resp.status_code == 200
    items = resp.json()
    assert items and all(i["category"] == "toys" for i in items)
    assert client.get("/api/products/does-not-exist").status_code == 404


def test_index_and_static_served(client):
    assert "Bazaaro" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


def test_testbed_apply_rejects_invalid_config(client):
    resp = client.post("/api/testbed/apply", json={"checkout_workers": 99})
    assert resp.status_code == 422


def test_load_test_refused_when_testbed_not_running(client):
    resp = client.post("/api/lab/tests", json={"kind": "steady", "params": {"rps": 5}})
    assert resp.status_code == 409


def test_slo_is_configurable(client):
    resp = client.post("/api/lab/slo", json={"p95_ms": 300, "error_pct": 2})
    assert resp.json() == {"p95_ms": 300.0, "error_pct": 2.0}
    client.post("/api/lab/slo", json={"p95_ms": 500, "error_pct": 1})


# -- testbed config ----------------------------------------------------------


def test_testbed_config_validation():
    assert TestbedConfig.from_dict({"checkout_workers": "3"}).checkout_workers == 3
    with pytest.raises(ValueError):
        TestbedConfig.from_dict({"payment_failure_rate": 1.5})
    with pytest.raises(ValueError):
        TestbedConfig.from_dict({"inventory_workers": 0})


# -- model what-if -------------------------------------------------------------


def test_what_if_boundary_grows_with_replicas_and_cpu():
    result = what_if(WhatIfConfig(replicas=4, cpu_cores=0.5, target_rps=200))
    assert result["label"] == "model (simulated)"
    by_rep = [b["max_safe_rps"] for b in result["boundary_by_replicas"]]
    assert by_rep == sorted(by_rep), "capacity must not fall as replicas are added"
    by_cpu = [b["max_safe_rps"] for b in result["boundary_by_cpu"]]
    assert by_cpu == sorted(by_cpu)
    assert result["capacity"]["max_replicas_by_pool"] == 50 // 6


def test_what_if_flags_connection_invariant():
    result = what_if(WhatIfConfig(replicas=10, db_pool_size=30, target_rps=100))
    assert any("K01" in w for w in result["warnings"])
    k01 = next(c for c in result["constraints"] if c["id"] == "K01")
    assert k01["fires"] is True


def test_what_if_overload_violates_slo():
    result = what_if(WhatIfConfig(replicas=1, cpu_cores=0.5, target_rps=500))
    assert result["slo_met"] is False


def test_registry_has_mode_classes():
    registry = load_constraint_registry()
    assert registry, "constraint_summary.json should provide at least one rule"
    for rec in registry:
        assert set(rec["classes"]) == {"static", "hpa", "vertical"}


# -- live-test summarisation ---------------------------------------------------


def _step(rps: float, p95: float, err: float = 0.0) -> dict:
    return {
        "offered_rps": rps,
        "checkout_p95_ms": p95,
        "error_pct": err,
        "client_saturated": False,
        "meets_slo": p95 <= 500 and err <= 1,
    }


def test_sweep_summary_reports_bound_and_region():
    test = LoadTest(id="t", kind="sweep", params={})
    test.steps = [_step(50, 80), _step(100, 90), _step(200, 900)]
    out = LoadRunner()._summarise(test, {"checkout_workers": 1}, SLO())
    bound = out["observed_bound"]
    assert bound["last_rps_meeting_slo"] == 100
    assert bound["first_rps_violating_slo"] == 200
    assert bound["tested_region"]["rps"] == [50, 200]


def test_sweep_summary_reports_disappeared_within_region():
    test = LoadTest(id="t", kind="sweep", params={})
    test.steps = [_step(50, 80), _step(100, 90)]
    out = LoadRunner()._summarise(test, {}, SLO())
    assert "disappeared within tested region" in out["observed_bound"]["constraint"]


def test_load_products_exclude_scarce_items():
    assert all(p.stock >= 1000 for p in LoadRunner.LOAD_PRODUCTS)
    assert len(LoadRunner.LOAD_PRODUCTS) < len(PRODUCTS)
