from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .. import __version__
from ..processing.engine import engine
from . import routes_documents, routes_export, routes_jobs, routes_processing, routes_projects, routes_search, routes_settings
from .deps import require_token

log = logging.getLogger(__name__)


def create_app(start_engine: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):  # noqa: ANN202
        if start_engine:
            engine.start()
        yield
        if start_engine:
            engine.stop()

    app = FastAPI(title="Marine Maintenance Intelligence API", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    # The renderer is loaded from file:// (origin "null") in production and from the Vite
    # dev server in development. Every request additionally needs the per-launch token.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(null|http://(localhost|127\.0\.0\.1):\d+)$",
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled API error")
        return JSONResponse(status_code=500, content={"detail": f"Internal error: {type(exc).__name__}. See the application log."})

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": __version__}

    deps = [Depends(require_token)]
    for module in (routes_projects, routes_documents, routes_processing, routes_jobs, routes_search, routes_export, routes_settings):
        app.include_router(module.router, prefix="/api", dependencies=deps)
    return app
