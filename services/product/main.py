"""Product Service for SCOPE-X."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from services.common.cache import CacheClient
from services.common.logging import setup_logger
from services.common.metrics import PrometheusMiddleware, metrics_endpoint

SERVICE_NAME = "product-service"
logger = setup_logger(SERVICE_NAME, level=os.getenv("LOG_LEVEL", "INFO"))


class Product(BaseModel):
    id: str
    name: str
    price: float = Field(gt=0)
    category: str
    description: str = ""


# In-memory primary store seeded with initial catalog
CATALOG: dict[str, Product] = {
    "prod-1": Product(
        id="prod-1",
        name="Cloud Native Hoodie",
        price=49.99,
        category="apparel",
        description="Warm hoodie",
    ),
    "prod-2": Product(
        id="prod-2",
        name="Kubernetes Mug",
        price=14.99,
        category="accessories",
        description="Ceramic mug",
    ),
    "prod-3": Product(
        id="prod-3",
        name="Mechanical Keyboard",
        price=129.99,
        category="electronics",
        description="RGB Keyboard",
    ),
}

cache = CacheClient()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Product Service initializing...")
    yield
    logger.info("Product Service shutting down...")


app = FastAPI(title="Product Service", lifespan=lifespan)
app.add_middleware(PrometheusMiddleware, service_name=SERVICE_NAME)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
def metrics() -> None:
    return metrics_endpoint()


@app.get("/products", response_model=list[Product])
def list_products() -> list[Product]:
    cached = cache.get("all_products")
    if cached is not None:
        logger.info("Returning products from cache")
        return [Product(**p) for p in cached]

    products = list(CATALOG.values())
    cache.set("all_products", [p.model_dump() for p in products], ttl=60)
    logger.info("Fetched %d products from primary catalog", len(products))
    return products


@app.get("/products/{product_id}", response_model=Product)
def get_product(product_id: str) -> Product:
    cache_key = f"product:{product_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        logger.info("Cache hit for product %s", product_id)
        return Product(**cached)

    product = CATALOG.get(product_id)
    if not product:
        logger.warning("Product not found: %s", product_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    cache.set(cache_key, product.model_dump(), ttl=120)
    logger.info("Cache miss for product %s; cached", product_id)
    return product


@app.post("/products", response_model=Product, status_code=status.HTTP_201_CREATED)
def create_product(product: Product) -> Product:
    if product.id in CATALOG:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Product with id {product.id} already exists",
        )
    CATALOG[product.id] = product
    cache.set(f"product:{product.id}", product.model_dump(), ttl=120)
    cache.set("all_products", [p.model_dump() for p in CATALOG.values()], ttl=60)
    logger.info("Product created: %s", product.id)
    return product


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)
