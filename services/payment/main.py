"""Simulated Payment Service for SCOPE-X with configurable latency and failure rates."""

import asyncio
import os
import random
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from services.common.logging import setup_logger
from services.common.metrics import PrometheusMiddleware, metrics_endpoint

SERVICE_NAME = "payment-service"
logger = setup_logger(SERVICE_NAME, level=os.getenv("LOG_LEVEL", "INFO"))


class PaymentRequest(BaseModel):
    cart_id: str
    amount: float = Field(gt=0)
    currency: str = "USD"
    payment_method: str = "credit_card"


class PaymentResponse(BaseModel):
    transaction_id: str
    status: str
    amount: float
    currency: str


def get_latency_ms() -> float:
    return float(os.getenv("PAYMENT_LATENCY_MS", "50"))


def get_failure_rate() -> float:
    return float(os.getenv("PAYMENT_FAILURE_RATE", "0.0"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info(
        "Payment Service initialized (latency=%.1fms, failure_rate=%.2f)",
        get_latency_ms(),
        get_failure_rate(),
    )
    yield
    logger.info("Payment Service shutting down...")


app = FastAPI(title="Payment Service", lifespan=lifespan)
app.add_middleware(PrometheusMiddleware, service_name=SERVICE_NAME)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "latency_ms": str(get_latency_ms()),
        "failure_rate": str(get_failure_rate()),
    }


@app.get("/metrics")
def metrics() -> None:
    return metrics_endpoint()


@app.post("/payment/process", response_model=PaymentResponse)
async def process_payment(request: PaymentRequest) -> PaymentResponse:
    latency_ms = get_latency_ms()
    failure_rate = get_failure_rate()

    # Simulate network / gateway latency
    if latency_ms > 0:
        await asyncio.sleep(latency_ms / 1000.0)

    # Simulate payment failure
    if random.random() < failure_rate:
        logger.warning(
            "Simulated payment failure for cart %s, amount %.2f",
            request.cart_id,
            request.amount,
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Payment transaction declined by mock gateway",
        )

    txn_id = f"txn_{uuid.uuid4().hex[:12]}"
    logger.info("Payment approved: txn %s for $%.2f", txn_id, request.amount)
    return PaymentResponse(
        transaction_id=txn_id,
        status="succeeded",
        amount=request.amount,
        currency=request.currency,
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8004"))
    uvicorn.run(app, host="0.0.0.0", port=port)
