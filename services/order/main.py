"""Order Service for SCOPE-X backed by PostgreSQL and asynchronous message consumer."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Float, String, Text
from sqlalchemy.orm import Session

from services.common.database import Base, create_db_engine, get_db_pool_size, get_session_factory
from services.common.logging import setup_logger
from services.common.messaging import BROKER, QUEUE_NAME
from services.common.metrics import PrometheusMiddleware, instrument_db_pool, metrics_endpoint
from services.common.tracing import setup_tracer, trace_router, trace_span

SERVICE_NAME = "order-service"
logger = setup_logger(SERVICE_NAME, level=os.getenv("LOG_LEVEL", "INFO"))
tracer = setup_tracer(SERVICE_NAME)


# SQLAlchemy ORM Model
class OrderRecord(Base):
    __tablename__ = "orders"

    id = Column(String(64), primary_key=True, index=True)
    cart_id = Column(String(64), nullable=False)
    user_id = Column(String(64), nullable=False)
    total_amount = Column(Float, nullable=False)
    currency = Column(String(8), nullable=False, default="USD")
    status = Column(String(32), nullable=False, default="CONFIRMED")
    items_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))


# Pydantic Schemas
class OrderItem(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)
    unit_price: float = Field(gt=0)


class CreateOrderRequest(BaseModel):
    cart_id: str
    user_id: str = "guest_user"
    total_amount: float = Field(gt=0)
    currency: str = "USD"
    items: list[OrderItem]
    transaction_id: str = ""
    order_id: str = ""


class OrderResponse(BaseModel):
    order_id: str
    cart_id: str
    user_id: str
    total_amount: float
    currency: str
    status: str
    created_at: str
    items: list[OrderItem]


class ConsumerConfig(BaseModel):
    count: int = Field(ge=0, le=100)


# Database initialization with DB_POOL_SIZE
db_pool_size = get_db_pool_size()
db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/scopex")
engine = create_db_engine(db_url=db_url, pool_size=db_pool_size)
SessionLocal = get_session_factory(engine)


def init_db() -> None:
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Order database initialized with DB_POOL_SIZE=%d", db_pool_size)
    except Exception as exc:
        logger.warning("Could not initialize order database (%s): %s", db_url, exc)


# Ensure tables exist immediately if database is reachable
init_db()
instrument_db_pool(engine, SERVICE_NAME, pool_size=db_pool_size)


async def process_order_message(data: dict[str, Any]) -> None:
    """Consumer worker callback: processes order creation event asynchronously."""
    order_id = data.get("order_id", f"ord_{uuid.uuid4().hex[:12]}")
    carrier = data.get("trace_headers", {})

    # Simulate realistic processing / database transaction delay
    delay_ms = float(os.getenv("ORDER_PROCESSING_DELAY_MS", "50"))
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000.0)

    with trace_span(tracer, "order.consume_and_create", carrier=carrier):
        with SessionLocal() as db:
            items_dump = json.dumps(data.get("items", []))
            record = OrderRecord(
                id=order_id,
                cart_id=data.get("cart_id", ""),
                user_id=data.get("user_id", "guest"),
                total_amount=float(data.get("total_amount", 0.0)),
                currency=data.get("currency", "USD"),
                status="CONFIRMED",
                items_json=items_dump,
                created_at=datetime.now(UTC),
            )
            db.merge(record)
            db.commit()

    logger.info(
        "Async consumer finalized order: %s ($%.2f)", order_id, data.get("total_amount", 0.0)
    )


def get_db_session() -> AsyncGenerator[Session, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Order Service starting (DB_POOL_SIZE=%d)...", db_pool_size)
    init_db()
    await BROKER.connect()

    initial_consumers = int(os.getenv("ORDER_CONSUMER_CONCURRENCY", "1"))
    await BROKER.set_consumer_concurrency(initial_consumers, process_order_message)
    logger.info("Order consumers initialized: %d workers", initial_consumers)

    yield

    logger.info("Order Service shutting down consumers...")
    await BROKER.stop()


app = FastAPI(title="Order Service", lifespan=lifespan)
app.add_middleware(PrometheusMiddleware, service_name=SERVICE_NAME)
app.include_router(trace_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "pool_size": str(db_pool_size),
        "consumers": str(BROKER._active_consumer_count),
    }


@app.get("/metrics")
def metrics() -> None:
    return metrics_endpoint()


@app.get("/orders/backlog")
async def get_backlog() -> dict[str, Any]:
    backlog = await BROKER.get_backlog(QUEUE_NAME)
    return {
        "queue": QUEUE_NAME,
        "backlog": backlog,
        "active_consumers": BROKER._active_consumer_count,
    }


@app.post("/orders/consumers")
async def set_consumers(config: ConsumerConfig) -> dict[str, Any]:
    await BROKER.set_consumer_concurrency(config.count, process_order_message)
    return {
        "status": "updated",
        "active_consumers": BROKER._active_consumer_count,
    }


@app.post("/orders", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    request: CreateOrderRequest,
    raw_req: Request,
) -> OrderResponse:
    """Asynchronously enqueues order creation request to RabbitMQ / async queue."""
    order_id = request.order_id or f"ord_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)

    payload = {
        "order_id": order_id,
        "cart_id": request.cart_id,
        "user_id": request.user_id,
        "total_amount": request.total_amount,
        "currency": request.currency,
        "items": [item.model_dump() for item in request.items],
        "transaction_id": request.transaction_id,
        "trace_headers": dict(raw_req.headers),
    }

    # Asynchronously publish to message queue
    with trace_span(tracer, "order.publish_event", carrier=dict(raw_req.headers)):
        await BROKER.publish(QUEUE_NAME, payload)

    logger.info("Enqueued order creation event for %s ($%.2f)", order_id, request.total_amount)
    return OrderResponse(
        order_id=order_id,
        cart_id=request.cart_id,
        user_id=request.user_id,
        total_amount=request.total_amount,
        currency=request.currency,
        status="PENDING",
        created_at=now.isoformat(),
        items=request.items,
    )


@app.get("/orders/{order_id}", response_model=OrderResponse)
def get_order(order_id: str, db: Session = Depends(get_db_session)) -> OrderResponse:
    order = db.query(OrderRecord).filter_by(id=order_id).first()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    raw_items = json.loads(order.items_json)
    items = [OrderItem(**i) for i in raw_items]
    return OrderResponse(
        order_id=order.id,
        cart_id=order.cart_id,
        user_id=order.user_id,
        total_amount=order.total_amount,
        currency=order.currency,
        status=order.status,
        created_at=order.created_at.isoformat(),
        items=items,
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8005"))
    uvicorn.run(app, host="0.0.0.0", port=port)
