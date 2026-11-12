"""Order Service for SCOPE-X backed by PostgreSQL with configurable DB_POOL_SIZE."""

import json
import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Float, String, Text
from sqlalchemy.orm import Session

from services.common.database import Base, create_db_engine, get_db_pool_size, get_session_factory
from services.common.logging import setup_logger
from services.common.metrics import PrometheusMiddleware, metrics_endpoint

SERVICE_NAME = "order-service"
logger = setup_logger(SERVICE_NAME, level=os.getenv("LOG_LEVEL", "INFO"))


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


class OrderResponse(BaseModel):
    order_id: str
    cart_id: str
    user_id: str
    total_amount: float
    currency: str
    status: str
    created_at: str
    items: list[OrderItem]


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
    yield
    logger.info("Order Service shutting down...")


app = FastAPI(title="Order Service", lifespan=lifespan)
app.add_middleware(PrometheusMiddleware, service_name=SERVICE_NAME)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME, "pool_size": str(db_pool_size)}


@app.get("/metrics")
def metrics() -> None:
    return metrics_endpoint()


@app.post("/orders", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(
    request: CreateOrderRequest, db: Session = Depends(get_db_session)
) -> OrderResponse:
    order_id = f"ord_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)
    items_dump = json.dumps([item.model_dump() for item in request.items])

    order = OrderRecord(
        id=order_id,
        cart_id=request.cart_id,
        user_id=request.user_id,
        total_amount=request.total_amount,
        currency=request.currency,
        status="CONFIRMED",
        items_json=items_dump,
        created_at=now,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    logger.info(
        "Order created: %s for user %s ($%.2f)", order_id, request.user_id, request.total_amount
    )
    return OrderResponse(
        order_id=order.id,
        cart_id=order.cart_id,
        user_id=order.user_id,
        total_amount=order.total_amount,
        currency=order.currency,
        status=order.status,
        created_at=order.created_at.isoformat(),
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
