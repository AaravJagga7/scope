"""Inventory Service for SCOPE-X backed by PostgreSQL with configurable DB_POOL_SIZE."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import Session

from services.common.database import Base, create_db_engine, get_db_pool_size, get_session_factory
from services.common.logging import setup_logger
from services.common.metrics import PrometheusMiddleware, metrics_endpoint

SERVICE_NAME = "inventory-service"
logger = setup_logger(SERVICE_NAME, level=os.getenv("LOG_LEVEL", "INFO"))


# SQLAlchemy ORM Model
class InventoryRecord(Base):
    __tablename__ = "inventory"

    product_id = Column(String(64), primary_key=True, index=True)
    available_qty = Column(Integer, nullable=False, default=0)
    reserved_qty = Column(Integer, nullable=False, default=0)


# Pydantic Schemas
class InventoryItem(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)


class StockStatus(BaseModel):
    product_id: str
    available_qty: int
    reserved_qty: int


class ReservationRequest(BaseModel):
    items: list[InventoryItem]


class ReservationResponse(BaseModel):
    success: bool
    reserved_items: list[InventoryItem]


# Database initialization with DB_POOL_SIZE
db_pool_size = get_db_pool_size()
db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/scopex")
engine = create_db_engine(db_url=db_url, pool_size=db_pool_size)
SessionLocal = get_session_factory(engine)


def init_db() -> None:
    """Initialize inventory table and seed baseline stock."""
    try:
        Base.metadata.create_all(bind=engine)
        with SessionLocal() as session:
            seed_items = {
                "prod-1": 100,
                "prod-2": 50,
                "prod-3": 25,
            }
            for pid, qty in seed_items.items():
                record = session.query(InventoryRecord).filter_by(product_id=pid).first()
                if not record:
                    session.add(InventoryRecord(product_id=pid, available_qty=qty, reserved_qty=0))
            session.commit()
        logger.info("Inventory database initialized with DB_POOL_SIZE=%d", db_pool_size)
    except Exception as exc:
        logger.warning(
            "Could not connect/initialize database (%s): %s. Tables will be created upon first connection.",
            db_url,
            exc,
        )


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
    logger.info("Inventory Service starting (DB_POOL_SIZE=%d)...", db_pool_size)
    init_db()
    yield
    logger.info("Inventory Service shutting down...")


app = FastAPI(title="Inventory Service", lifespan=lifespan)
app.add_middleware(PrometheusMiddleware, service_name=SERVICE_NAME)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME, "pool_size": str(db_pool_size)}


@app.get("/metrics")
def metrics() -> None:
    return metrics_endpoint()


@app.get("/inventory/{product_id}", response_model=StockStatus)
def get_inventory(product_id: str, db: Session = Depends(get_db_session)) -> StockStatus:
    record = db.query(InventoryRecord).filter_by(product_id=product_id).first()
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product {product_id} not found in inventory",
        )
    return StockStatus(
        product_id=record.product_id,
        available_qty=record.available_qty,
        reserved_qty=record.reserved_qty,
    )


@app.post("/inventory/reserve", response_model=ReservationResponse)
def reserve_inventory(
    request: ReservationRequest, db: Session = Depends(get_db_session)
) -> ReservationResponse:
    """Reserve inventory for checkout; rolls back if insufficient stock for any item."""
    for item in request.items:
        record = (
            db.query(InventoryRecord)
            .filter_by(product_id=item.product_id)
            .with_for_update()
            .first()
        )
        if not record or record.available_qty < item.quantity:
            available = record.available_qty if record else 0
            logger.warning(
                "Insufficient inventory for %s: requested %d, available %d",
                item.product_id,
                item.quantity,
                available,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Insufficient inventory for product {item.product_id}",
            )

    # Perform reservations
    for item in request.items:
        record = db.query(InventoryRecord).filter_by(product_id=item.product_id).first()
        if record:
            record.available_qty -= item.quantity
            record.reserved_qty += item.quantity

    db.commit()
    logger.info("Reserved %d inventory line items", len(request.items))
    return ReservationResponse(success=True, reserved_items=request.items)


@app.post("/inventory/deduct", response_model=ReservationResponse)
def deduct_inventory(
    request: ReservationRequest, db: Session = Depends(get_db_session)
) -> ReservationResponse:
    """Commit reservation by deducting from reserved quantities."""
    for item in request.items:
        record = (
            db.query(InventoryRecord)
            .filter_by(product_id=item.product_id)
            .with_for_update()
            .first()
        )
        if record:
            record.reserved_qty = max(0, record.reserved_qty - item.quantity)
    db.commit()
    logger.info("Deducted %d inventory line items", len(request.items))
    return ReservationResponse(success=True, reserved_items=request.items)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8003"))
    uvicorn.run(app, host="0.0.0.0", port=port)
