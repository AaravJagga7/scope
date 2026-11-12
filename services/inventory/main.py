"""Inventory Service for SCOPE-X backed by PostgreSQL with configurable DB_POOL_SIZE."""

import asyncio
import contextlib
import json
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import Column, Float, Integer, String
from sqlalchemy.orm import Session

from services.common.database import Base, create_db_engine, get_db_pool_size, get_session_factory
from services.common.logging import setup_logger
from services.common.metrics import PrometheusMiddleware, instrument_db_pool, metrics_endpoint
from services.common.tracing import setup_tracer, trace_router, trace_span

SERVICE_NAME = "inventory-service"
logger = setup_logger(SERVICE_NAME, level=os.getenv("LOG_LEVEL", "INFO"))
tracer = setup_tracer(SERVICE_NAME)


# SQLAlchemy ORM Model
class InventoryRecord(Base):
    __tablename__ = "inventory"

    product_id = Column(String(64), primary_key=True, index=True)
    available_qty = Column(Integer, nullable=False, default=0)
    reserved_qty = Column(Integer, nullable=False, default=0)


class InventoryHold(Base):
    """A time-limited stock reservation owned by one cart (``hold_id``).

    Status moves held -> locked (checkout in progress) -> committed (sold), or
    held/locked -> released (cart emptied) / expired (TTL passed, stock returned).
    """

    __tablename__ = "inventory_holds"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hold_id = Column(String(64), index=True, nullable=False)
    product_id = Column(String(64), index=True, nullable=False)
    quantity = Column(Integer, nullable=False)
    status = Column(String(16), index=True, nullable=False, default="held")
    created_at = Column(Float, nullable=False)
    expires_at = Column(Float, index=True, nullable=False)


ACTIVE_HOLD_STATUSES = ("held", "locked")
#: Extra time a hold gets once checkout starts, so it cannot expire mid-payment.
CHECKOUT_LOCK_GRACE_S = float(os.getenv("INVENTORY_CHECKOUT_LOCK_GRACE_S", "120"))
ADMIN_ENABLED = os.getenv("INVENTORY_ADMIN_ENABLED", "false").lower() == "true"


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


class HoldRequest(BaseModel):
    hold_id: str = Field(min_length=1, max_length=64)
    product_id: str
    quantity: int = Field(gt=0, le=100)
    ttl_seconds: float = Field(default=300.0, gt=0, le=3600)
    #: Purchase limit per hold for this product (None = unlimited).
    max_per_hold: int | None = Field(default=None, gt=0)


class HoldLine(BaseModel):
    product_id: str
    quantity: int
    status: str
    expires_at: float
    seconds_left: float


class HoldView(BaseModel):
    hold_id: str
    lines: list[HoldLine]


class HoldItemsRequest(BaseModel):
    items: list[InventoryItem]


class StockReset(BaseModel):
    product_id: str
    available_qty: int = Field(ge=0)


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
            # Optional extra catalog stock, e.g. '{"bz-101": 500}' (used by the storefront).
            extra_seed = os.getenv("INVENTORY_SEED_JSON", "").strip()
            if extra_seed:
                try:
                    seed_items.update({str(k): int(v) for k, v in json.loads(extra_seed).items()})
                except (ValueError, AttributeError) as exc:
                    logger.warning("Ignoring invalid INVENTORY_SEED_JSON: %s", exc)
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
instrument_db_pool(engine, SERVICE_NAME, pool_size=db_pool_size)


def _reserve_atomic(db: Session, product_id: str, quantity: int) -> bool:
    """Moves *quantity* from available to reserved only if enough is available.

    A single conditional UPDATE, so concurrent requests (threads or worker
    processes) can never drive stock negative or sell the same unit twice.
    """
    updated = (
        db.query(InventoryRecord)
        .filter(
            InventoryRecord.product_id == product_id,
            InventoryRecord.available_qty >= quantity,
        )
        .update(
            {
                InventoryRecord.available_qty: InventoryRecord.available_qty - quantity,
                InventoryRecord.reserved_qty: InventoryRecord.reserved_qty + quantity,
            },
            synchronize_session=False,
        )
    )
    return updated == 1


def _return_to_available(db: Session, product_id: str, quantity: int) -> None:
    db.query(InventoryRecord).filter(InventoryRecord.product_id == product_id).update(
        {
            InventoryRecord.available_qty: InventoryRecord.available_qty + quantity,
            InventoryRecord.reserved_qty: InventoryRecord.reserved_qty - quantity,
        },
        synchronize_session=False,
    )


def _end_hold_row(db: Session, row_id: int, new_status: str, from_statuses: tuple) -> bool:
    """Transitions one hold row; True only for the caller that won the transition."""
    updated = (
        db.query(InventoryHold)
        .filter(InventoryHold.id == row_id, InventoryHold.status.in_(from_statuses))
        .update({InventoryHold.status: new_status}, synchronize_session=False)
    )
    return updated == 1


def sweep_expired_holds(db: Session, now: float | None = None) -> int:
    """Expires holds past their TTL and returns their stock. Returns units released."""
    now = time.time() if now is None else now
    rows = (
        db.query(InventoryHold.id, InventoryHold.product_id, InventoryHold.quantity)
        .filter(
            InventoryHold.status.in_(ACTIVE_HOLD_STATUSES),
            InventoryHold.expires_at < now,
        )
        .limit(500)
        .all()
    )
    released = 0
    for row_id, product_id, qty in rows:
        if _end_hold_row(db, row_id, "expired", ACTIVE_HOLD_STATUSES):
            _return_to_available(db, product_id, qty)
            released += qty
    if rows:
        db.commit()
    return released


_last_sweep = 0.0


def _maybe_sweep(db: Session, min_interval: float = 1.0) -> None:
    global _last_sweep
    now = time.time()
    if now - _last_sweep >= min_interval:
        _last_sweep = now
        sweep_expired_holds(db, now)


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
    sweeper = asyncio.create_task(_sweep_loop())
    yield
    sweeper.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await sweeper
    logger.info("Inventory Service shutting down...")


async def _sweep_loop(interval: float = 1.0) -> None:
    """Returns expired reservations to stock even when no requests arrive."""
    while True:
        await asyncio.sleep(interval)
        try:
            with SessionLocal() as session:
                await asyncio.to_thread(sweep_expired_holds, session)
        except Exception as exc:  # noqa: BLE001 - keep sweeping on transient DB errors
            logger.warning("Hold sweep failed: %s", exc)


app = FastAPI(title="Inventory Service", lifespan=lifespan)
app.add_middleware(PrometheusMiddleware, service_name=SERVICE_NAME)
app.include_router(trace_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME, "pool_size": str(db_pool_size)}


@app.get("/metrics")
def metrics() -> None:
    return metrics_endpoint()


@app.get("/inventory/{product_id}", response_model=StockStatus)
def get_inventory(product_id: str, db: Session = Depends(get_db_session)) -> StockStatus:
    _maybe_sweep(db)
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
    request: ReservationRequest,
    raw_req: Request,
    db: Session = Depends(get_db_session),
) -> ReservationResponse:
    """Reserve inventory for checkout; rolls back if insufficient stock for any item."""
    with trace_span(tracer, "inventory.reserve", carrier=dict(raw_req.headers)):
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
    request: ReservationRequest,
    raw_req: Request,
    db: Session = Depends(get_db_session),
) -> ReservationResponse:
    """Commit reservation by deducting from reserved quantities."""
    with trace_span(tracer, "inventory.deduct", carrier=dict(raw_req.headers)):
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


# ---------------------------------------------------------------------------
# Time-limited holds (stock reservation with TTL)
# ---------------------------------------------------------------------------


def _hold_view(db: Session, hold_id: str) -> HoldView:
    now = time.time()
    rows = (
        db.query(InventoryHold)
        .filter(InventoryHold.hold_id == hold_id)
        .order_by(InventoryHold.id)
        .all()
    )
    latest: dict[str, InventoryHold] = {}
    active: dict[str, int] = {}
    for row in rows:
        latest[row.product_id] = row
        if row.status in ACTIVE_HOLD_STATUSES:
            active[row.product_id] = active.get(row.product_id, 0) + row.quantity
    lines = []
    for pid, row in latest.items():
        actives = [r for r in rows if r.product_id == pid and r.status in ACTIVE_HOLD_STATUSES]
        expires = min((r.expires_at for r in actives), default=row.expires_at)
        lines.append(
            HoldLine(
                product_id=pid,
                quantity=active.get(pid, row.quantity),
                status=actives[0].status if actives else row.status,
                expires_at=expires,
                seconds_left=round(max(0.0, expires - now), 1) if actives else 0.0,
            )
        )
    return HoldView(hold_id=hold_id, lines=lines)


@app.post("/inventory/holds", response_model=HoldView)
def create_hold(request: HoldRequest, db: Session = Depends(get_db_session)) -> HoldView:
    """Reserves stock for one cart for ``ttl_seconds``; 409 when not enough is free."""
    _maybe_sweep(db)
    now = time.time()
    if request.max_per_hold is not None:
        held = sum(
            r.quantity
            for r in db.query(InventoryHold).filter(
                InventoryHold.hold_id == request.hold_id,
                InventoryHold.product_id == request.product_id,
                InventoryHold.status.in_(ACTIVE_HOLD_STATUSES + ("committed",)),
            )
        )
        if held + request.quantity > request.max_per_hold:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Limit {request.max_per_hold} per customer for {request.product_id}",
            )
    if not _reserve_atomic(db, request.product_id, request.quantity):
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Sold out: no unreserved stock left for {request.product_id}",
        )
    db.add(
        InventoryHold(
            hold_id=request.hold_id,
            product_id=request.product_id,
            quantity=request.quantity,
            status="held",
            created_at=now,
            expires_at=now + request.ttl_seconds,
        )
    )
    db.commit()
    return _hold_view(db, request.hold_id)


@app.get("/inventory/holds/{hold_id}", response_model=HoldView)
def get_hold(hold_id: str, db: Session = Depends(get_db_session)) -> HoldView:
    _maybe_sweep(db)
    return _hold_view(db, hold_id)


def _active_rows(db: Session, hold_id: str) -> list[InventoryHold]:
    return (
        db.query(InventoryHold)
        .filter(
            InventoryHold.hold_id == hold_id,
            InventoryHold.status.in_(ACTIVE_HOLD_STATUSES),
        )
        .all()
    )


@app.post("/inventory/holds/{hold_id}/lock", response_model=HoldView)
def lock_hold(
    hold_id: str, request: HoldItemsRequest, db: Session = Depends(get_db_session)
) -> HoldView:
    """Checks the hold covers *items* and extends it for the checkout's duration."""
    sweep_expired_holds(db)
    now = time.time()
    rows = _active_rows(db, hold_id)
    for item in request.items:
        covered = sum(r.quantity for r in rows if r.product_id == item.product_id)
        if covered < item.quantity:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Reservation expired or missing for {item.product_id} "
                    f"(held {covered}, need {item.quantity})"
                ),
            )
    for r in rows:
        r.status = "locked"
        r.expires_at = max(r.expires_at, now + CHECKOUT_LOCK_GRACE_S)
    db.commit()
    return _hold_view(db, hold_id)


@app.post("/inventory/holds/{hold_id}/commit", response_model=HoldView)
def commit_hold(
    hold_id: str, request: HoldItemsRequest, db: Session = Depends(get_db_session)
) -> HoldView:
    """Marks held units as sold (reserved -> deducted); releases any extra units."""
    needed = {i.product_id: i.quantity for i in request.items}
    for row in _active_rows(db, hold_id):
        take = min(row.quantity, needed.get(row.product_id, 0))
        if _end_hold_row(db, row.id, "committed", ACTIVE_HOLD_STATUSES):
            needed[row.product_id] = needed.get(row.product_id, 0) - take
            db.query(InventoryRecord).filter(InventoryRecord.product_id == row.product_id).update(
                {InventoryRecord.reserved_qty: InventoryRecord.reserved_qty - row.quantity},
                synchronize_session=False,
            )
            if take < row.quantity:  # surplus units go back on sale
                db.query(InventoryRecord).filter(
                    InventoryRecord.product_id == row.product_id
                ).update(
                    {
                        InventoryRecord.available_qty: InventoryRecord.available_qty
                        + (row.quantity - take)
                    },
                    synchronize_session=False,
                )
                row.quantity = take
    db.commit()
    return _hold_view(db, hold_id)


@app.delete("/inventory/holds/{hold_id}", response_model=HoldView)
def release_hold(hold_id: str, db: Session = Depends(get_db_session)) -> HoldView:
    """Releases every active hold row of a cart and returns the stock."""
    for row in _active_rows(db, hold_id):
        if _end_hold_row(db, row.id, "released", ("held",)):
            _return_to_available(db, row.product_id, row.quantity)
    db.commit()
    return _hold_view(db, hold_id)


@app.get("/inventory/holds-stats/{product_id}")
def hold_stats(product_id: str, db: Session = Depends(get_db_session)) -> dict:
    """Units per hold status for one product (used by the flash-sale drop test)."""
    _maybe_sweep(db)
    out = {
        s: {"holds": 0, "units": 0} for s in ("held", "locked", "committed", "released", "expired")
    }
    for row in db.query(InventoryHold).filter(InventoryHold.product_id == product_id):
        bucket = out.setdefault(row.status, {"holds": 0, "units": 0})
        bucket["holds"] += 1
        bucket["units"] += row.quantity
    record = db.query(InventoryRecord).filter_by(product_id=product_id).first()
    return {
        "product_id": product_id,
        "available_qty": record.available_qty if record else None,
        "reserved_qty": record.reserved_qty if record else None,
        "by_status": out,
    }


@app.post("/inventory/admin/reset")
def admin_reset(request: StockReset, db: Session = Depends(get_db_session)) -> StockStatus:
    """Testbed-only: sets a product's stock and deletes its holds (a known baseline)."""
    if not ADMIN_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin API disabled")
    db.query(InventoryHold).filter(InventoryHold.product_id == request.product_id).delete(
        synchronize_session=False
    )
    record = db.query(InventoryRecord).filter_by(product_id=request.product_id).first()
    if record is None:
        record = InventoryRecord(product_id=request.product_id, available_qty=0, reserved_qty=0)
        db.add(record)
    record.available_qty = request.available_qty
    record.reserved_qty = 0
    db.commit()
    return StockStatus(
        product_id=record.product_id,
        available_qty=record.available_qty,
        reserved_qty=record.reserved_qty,
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8003"))
    uvicorn.run(app, host="0.0.0.0", port=port)
