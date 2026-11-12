"""Checkout Service for SCOPE-X orchestrating the end-to-end checkout workflow."""

import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from services.common.logging import setup_logger
from services.common.metrics import PrometheusMiddleware, metrics_endpoint
from services.common.tracing import (
    GLOBAL_TRACE_STORE,
    format_trace_tree,
    inject_carrier,
    setup_tracer,
)

SERVICE_NAME = "checkout-service"
logger = setup_logger(SERVICE_NAME, level=os.getenv("LOG_LEVEL", "INFO"))
tracer = setup_tracer(SERVICE_NAME)

# Configurable Service URLs
CART_SERVICE_URL = os.getenv("CART_SERVICE_URL", "http://localhost:8002")
INVENTORY_SERVICE_URL = os.getenv("INVENTORY_SERVICE_URL", "http://localhost:8003")
PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://localhost:8004")
ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "http://localhost:8005")
REQUEST_TIMEOUT = float(os.getenv("CHECKOUT_TIMEOUT_SECONDS", "5.0"))


class CheckoutRequest(BaseModel):
    cart_id: str
    user_id: str = "guest_user"
    payment_method: str = "credit_card"
    #: When set, stock was already reserved at add-to-cart time (TTL hold); checkout
    #: locks and commits that hold instead of reserving again.
    hold_id: str | None = None


class CheckoutResponse(BaseModel):
    checkout_id: str
    order_id: str
    transaction_id: str
    status: str
    total_amount: float
    trace_id: str = ""
    items: list[dict[str, Any]] = Field(default_factory=list)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info(
        "Checkout Service initialized (Cart=%s, Inv=%s, Pay=%s, Order=%s)",
        CART_SERVICE_URL,
        INVENTORY_SERVICE_URL,
        PAYMENT_SERVICE_URL,
        ORDER_SERVICE_URL,
    )
    yield
    logger.info("Checkout Service shutting down...")


app = FastAPI(title="Checkout Service", lifespan=lifespan)
app.add_middleware(PrometheusMiddleware, service_name=SERVICE_NAME)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
def metrics() -> None:
    return metrics_endpoint()


async def aggregate_distributed_trace(trace_id: str) -> list[dict[str, Any]]:
    """Gathers and aggregates spans for trace_id across all participating services."""
    local_spans = GLOBAL_TRACE_STORE.get_trace(trace_id)
    all_spans = list(local_spans)
    seen_span_ids = {s["span_id"] for s in all_spans}

    remote_endpoints = [
        f"{CART_SERVICE_URL}/traces/{trace_id}",
        f"{INVENTORY_SERVICE_URL}/traces/{trace_id}",
        f"{PAYMENT_SERVICE_URL}/traces/{trace_id}",
        f"{ORDER_SERVICE_URL}/traces/{trace_id}",
    ]

    async with httpx.AsyncClient(timeout=2.0) as client:
        for ep in remote_endpoints:
            try:
                resp = await client.get(ep)
                if resp.status_code == 200:
                    data = resp.json()
                    for s in data.get("spans", []):
                        if s["span_id"] not in seen_span_ids:
                            all_spans.append(s)
                            seen_span_ids.add(s["span_id"])
            except Exception:
                pass

    return all_spans


@app.get("/traces/latest")
async def get_latest_trace() -> dict[str, Any]:
    trace_id = GLOBAL_TRACE_STORE.get_latest_trace_id()
    if not trace_id:
        return {"status": "no_traces", "message": "No traces recorded yet."}
    spans = await aggregate_distributed_trace(trace_id)
    return {
        "trace_id": trace_id,
        "formatted_tree": format_trace_tree(trace_id, spans),
        "spans": spans,
    }


@app.get("/traces/{trace_id}")
async def get_trace_by_id(trace_id: str) -> dict[str, Any]:
    spans = await aggregate_distributed_trace(trace_id)
    if not spans:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trace {trace_id} not found",
        )
    return {
        "trace_id": trace_id,
        "formatted_tree": format_trace_tree(trace_id, spans),
        "spans": spans,
    }


@app.post("/checkout", response_model=CheckoutResponse, status_code=status.HTTP_200_OK)
async def process_checkout(request: CheckoutRequest) -> CheckoutResponse:
    checkout_id = f"chk_{uuid.uuid4().hex[:12]}"
    logger.info("[%s] Beginning checkout workflow for cart: %s", checkout_id, request.cart_id)

    with tracer.start_as_current_span("checkout.orchestrate") as root_span:
        root_span.set_attribute("checkout.id", checkout_id)
        root_span.set_attribute("cart.id", request.cart_id)
        root_span.set_attribute("user.id", request.user_id)
        trace_id = format(root_span.get_span_context().trace_id, "032x")

        # Inject distributed trace context into outbound HTTP carrier
        trace_headers = inject_carrier({})

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            # Step 1: Validate cart
            try:
                cart_resp = await client.post(
                    f"{CART_SERVICE_URL}/cart/{request.cart_id}/validate",
                    headers=trace_headers,
                )
                if cart_resp.status_code != status.HTTP_200_OK:
                    logger.warning("[%s] Cart validation failed: %s", checkout_id, cart_resp.text)
                    raise HTTPException(
                        status_code=cart_resp.status_code,
                        detail=f"Cart validation failed: {cart_resp.text}",
                    )
                cart_data = cart_resp.json()
                items = cart_data.get("items", [])
                total_amount = float(cart_data.get("total_amount", 0.0))
                if not items or total_amount <= 0:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Cart has no valid items or non-positive total",
                    )
            except httpx.RequestError as exc:
                logger.error("[%s] Cart service unreachable: %s", checkout_id, exc)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Cart service unavailable: {exc}",
                ) from exc

            # Step 2: Check and reserve inventory
            reservation_payload = {
                "items": [{"product_id": i["product_id"], "quantity": i["quantity"]} for i in items]
            }
            reserve_url = (
                f"{INVENTORY_SERVICE_URL}/inventory/holds/{request.hold_id}/lock"
                if request.hold_id
                else f"{INVENTORY_SERVICE_URL}/inventory/reserve"
            )
            try:
                inv_resp = await client.post(
                    reserve_url,
                    json=reservation_payload,
                    headers=trace_headers,
                )
                if inv_resp.status_code != status.HTTP_200_OK:
                    logger.warning(
                        "[%s] Inventory reservation failed: %s", checkout_id, inv_resp.text
                    )
                    raise HTTPException(
                        status_code=inv_resp.status_code,
                        detail=f"Inventory reservation failed: {inv_resp.text}",
                    )
            except httpx.RequestError as exc:
                logger.error("[%s] Inventory service unreachable: %s", checkout_id, exc)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Inventory service unavailable: {exc}",
                ) from exc

            # Step 3: Call payment service
            payment_payload = {
                "cart_id": request.cart_id,
                "amount": total_amount,
                "currency": "USD",
                "payment_method": request.payment_method,
            }
            try:
                pay_resp = await client.post(
                    f"{PAYMENT_SERVICE_URL}/payment/process",
                    json=payment_payload,
                    headers=trace_headers,
                )
                if pay_resp.status_code != status.HTTP_200_OK:
                    logger.warning("[%s] Payment processing failed: %s", checkout_id, pay_resp.text)
                    raise HTTPException(
                        status_code=pay_resp.status_code,
                        detail=f"Payment failed: {pay_resp.text}",
                    )
                pay_data = pay_resp.json()
                transaction_id = pay_data.get("transaction_id", f"txn_{uuid.uuid4().hex[:10]}")
            except httpx.RequestError as exc:
                logger.error("[%s] Payment service unreachable: %s", checkout_id, exc)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Payment service unavailable: {exc}",
                ) from exc

            # Step 4: Create order
            order_payload = {
                "cart_id": request.cart_id,
                "user_id": request.user_id,
                "total_amount": total_amount,
                "currency": "USD",
                "items": items,
                "transaction_id": transaction_id,
            }
            try:
                order_resp = await client.post(
                    f"{ORDER_SERVICE_URL}/orders",
                    json=order_payload,
                    headers=trace_headers,
                )
                if order_resp.status_code not in (
                    status.HTTP_201_CREATED,
                    status.HTTP_202_ACCEPTED,
                ):
                    logger.error("[%s] Order creation failed: %s", checkout_id, order_resp.text)
                    raise HTTPException(
                        status_code=order_resp.status_code,
                        detail=f"Order creation failed: {order_resp.text}",
                    )
                order_data = order_resp.json()
                order_id = order_data["order_id"]
            except httpx.RequestError as exc:
                logger.error("[%s] Order service unreachable: %s", checkout_id, exc)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Order service unavailable: {exc}",
                ) from exc

            # Step 5: Finalize deduction and clear cart asynchronously/best-effort
            try:
                await client.post(
                    f"{INVENTORY_SERVICE_URL}/inventory/holds/{request.hold_id}/commit"
                    if request.hold_id
                    else f"{INVENTORY_SERVICE_URL}/inventory/deduct",
                    json=reservation_payload,
                    headers=trace_headers,
                )
                await client.delete(
                    f"{CART_SERVICE_URL}/cart/{request.cart_id}",
                    headers=trace_headers,
                )
            except Exception as exc:
                logger.warning("[%s] Post-checkout cleanup notice: %s", checkout_id, exc)

        root_span.set_attribute("order.id", order_id)
        root_span.set_attribute("payment.transaction_id", transaction_id)
        logger.info(
            "[%s] Checkout completed successfully. Order: %s, Txn: %s, Trace: %s",
            checkout_id,
            order_id,
            transaction_id,
            trace_id,
        )
        return CheckoutResponse(
            checkout_id=checkout_id,
            order_id=order_id,
            transaction_id=transaction_id,
            status="completed",
            total_amount=total_amount,
            trace_id=trace_id,
            items=items,
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8006"))
    uvicorn.run(app, host="0.0.0.0", port=port)
