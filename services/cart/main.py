"""Cart Service for SCOPE-X."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from services.common.logging import setup_logger
from services.common.metrics import PrometheusMiddleware, metrics_endpoint

SERVICE_NAME = "cart-service"
logger = setup_logger(SERVICE_NAME, level=os.getenv("LOG_LEVEL", "INFO"))


class CartItem(BaseModel):
    product_id: str
    product_name: str = ""
    quantity: int = Field(gt=0)
    unit_price: float = Field(gt=0)


class AddItemRequest(BaseModel):
    product_id: str
    product_name: str = ""
    quantity: int = Field(default=1, gt=0)
    unit_price: float = Field(gt=0)


class Cart(BaseModel):
    cart_id: str
    items: list[CartItem] = Field(default_factory=list)
    total_amount: float = 0.0


# In-memory storage for active cart sessions
CARTS: dict[str, dict[str, CartItem]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Cart Service initializing...")
    yield
    logger.info("Cart Service shutting down...")


app = FastAPI(title="Cart Service", lifespan=lifespan)
app.add_middleware(PrometheusMiddleware, service_name=SERVICE_NAME)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
def metrics() -> None:
    return metrics_endpoint()


@app.get("/cart/{cart_id}", response_model=Cart)
def get_cart(cart_id: str) -> Cart:
    items_map = CARTS.get(cart_id, {})
    items = list(items_map.values())
    total = sum(i.unit_price * i.quantity for i in items)
    return Cart(cart_id=cart_id, items=items, total_amount=round(total, 2))


@app.post("/cart/{cart_id}/items", response_model=Cart)
def add_item(cart_id: str, request: AddItemRequest) -> Cart:
    if cart_id not in CARTS:
        CARTS[cart_id] = {}

    items_map = CARTS[cart_id]
    if request.product_id in items_map:
        existing = items_map[request.product_id]
        items_map[request.product_id] = CartItem(
            product_id=request.product_id,
            product_name=request.product_name or existing.product_name,
            quantity=existing.quantity + request.quantity,
            unit_price=request.unit_price,
        )
    else:
        items_map[request.product_id] = CartItem(
            product_id=request.product_id,
            product_name=request.product_name,
            quantity=request.quantity,
            unit_price=request.unit_price,
        )

    logger.info("Added item %s (qty %d) to cart %s", request.product_id, request.quantity, cart_id)
    return get_cart(cart_id)


@app.delete("/cart/{cart_id}", status_code=status.HTTP_200_OK)
def clear_cart(cart_id: str) -> dict[str, str]:
    if cart_id in CARTS:
        CARTS.pop(cart_id, None)
    logger.info("Cleared cart %s", cart_id)
    return {"status": "cleared", "cart_id": cart_id}


@app.post("/cart/{cart_id}/validate", response_model=Cart)
def validate_cart(cart_id: str) -> Cart:
    items_map = CARTS.get(cart_id, {})
    if not items_map:
        logger.warning("Cart validation failed: cart %s is empty", cart_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cart {cart_id} is empty or does not exist",
        )

    cart = get_cart(cart_id)
    logger.info(
        "Cart %s successfully validated (%d items, total $%.2f)",
        cart_id,
        len(cart.items),
        cart.total_amount,
    )
    return cart


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8002"))
    uvicorn.run(app, host="0.0.0.0", port=port)
