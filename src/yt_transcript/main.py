import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import Settings
from .errors import ApiError, unauthorized
from .routes import router
from .services import open_services

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with open_services(settings, transport) as services:
            app.state.transcripts = services.transcripts
            app.state.listings = services.listings
            yield

    async def require_api_key(request: Request) -> None:
        if settings.api_key and request.headers.get("authorization") != f"Bearer {settings.api_key}":
            raise unauthorized()

    app = FastAPI(title="yt-transcript", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, err: ApiError) -> JSONResponse:
        return JSONResponse(err.body(), status_code=err.status, headers=err.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", ()))
        return JSONResponse(
            {"detail": f"{loc}: {first.get('msg', 'invalid request')}", "code": "validation_error"},
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def internal_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse({"detail": "Internal server error", "code": "internal_error"}, status_code=500)

    @app.get("/health")
    async def health(request: Request) -> dict:
        return {"status": "ok", "cooldowns": request.app.state.transcripts.cooldowns()}

    app.include_router(router, dependencies=[Depends(require_api_key)])
    return app


app = create_app()
