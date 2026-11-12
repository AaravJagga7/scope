"""Flash-sale Shield as a FastAPI service with a mock Checkout behind it.

Run:  uvicorn flash_sale_shield.main:app --port 8000

Endpoints
  POST /checkout      customer checkout attempt (goes through the shield)
  GET  /config        current shield + checkout parameters
  PUT  /config        change shield parameters at runtime (JSON body)
  GET  /stats         JSON snapshot (queue, tokens, latency percentiles...)
  GET  /metrics       Prometheus text format
  GET  /health        liveness
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from .config import CheckoutConfig, ShieldConfig
from .runtime import ShieldRuntime

runtime: ShieldRuntime | None = None


def _checkout_cfg_from_env() -> CheckoutConfig:
    cfg = CheckoutConfig()
    if "SCOPEX_REPLICAS" in os.environ:
        cfg.replicas = int(os.environ["SCOPEX_REPLICAS"])
    return cfg


@asynccontextmanager
async def lifespan(_: FastAPI):
    global runtime
    runtime = ShieldRuntime(ShieldConfig(), _checkout_cfg_from_env())
    runtime.start()
    yield
    await runtime.stop()


app = FastAPI(title="SCOPE-X Flash-sale Shield", lifespan=lifespan)


@app.post("/checkout")
async def checkout() -> JSONResponse:
    res = await runtime.submit()
    headers = {}
    if res["status"] == 503:
        headers["Retry-After"] = str(res.get("retry_after_s", 1))
    return JSONResponse(res, status_code=res["status"], headers=headers)


@app.get("/config")
async def get_config() -> Dict[str, Any]:
    return {"shield": runtime.core.config.to_dict(),
            "checkout": runtime.checkout.cfg.to_dict()}


@app.put("/config")
async def put_config(changes: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    try:
        return {"shield": runtime.reconfigure(**changes)}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/stats")
async def stats() -> Dict[str, Any]:
    return runtime.stats()


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> str:
    return runtime.prometheus()


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}
