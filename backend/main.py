"""
FastAPI application entry point.
"""
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api.routes.activity     import router as activity_router
from api.routes.campaigns    import router as campaigns_router
from api.routes.inbox        import router as inbox_router
from api.routes.leads        import router as leads_router
from api.routes.pain_signals import router as pain_signals_router
from db.database             import check_db_health, init_db, AsyncSessionLocal

logger = logging.getLogger(__name__)

API_KEY         = os.environ.get("API_SECRET_KEY", "")
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",")]
ENV             = os.environ.get("ENV", "development")

UNPROTECTED_PATHS = {"/health", "/", "/docs", "/openapi.json", "/redoc"}


async def api_key_middleware(request: Request, call_next):
    if API_KEY and request.url.path not in UNPROTECTED_PATHS:
        provided = request.headers.get("X-API-Key", "")
        if not provided:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "X-API-Key header required"},
            )
        import hmac
        if not hmac.compare_digest(provided, API_KEY):
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Invalid API key"},
            )
    return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Lead Engine API (env=%s)", ENV)

    # Init DB schema
    try:
        await init_db()
        logger.info("Database schema verified")
    except Exception as exc:
        logger.error("DB init error (non-fatal): %s", exc)

    # Sync inbox state from DB on startup
    try:
        from deliverability.inbox_rotation_manager import get_rotation_manager
        mgr = get_rotation_manager()
        async with AsyncSessionLocal() as db:
            await mgr.sync_from_db(db)
        logger.info("Inbox state synced (%d inboxes)", len(mgr.inboxes))
    except Exception as exc:
        logger.warning("Inbox sync error (non-fatal): %s", exc)

    # Recover stuck queue items
    try:
        from workers.email_sender import recover_stuck_sends
        async with AsyncSessionLocal() as db:
            recovered = await recover_stuck_sends(db)
            if recovered:
                logger.info("Recovered %d stuck queue items", recovered)
    except Exception as exc:
        logger.warning("Stuck-send recovery error (non-fatal): %s", exc)

    yield
    logger.info("Lead Engine API shutting down")


app = FastAPI(
    title="Autonomous Lead Engine API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if ENV != "production" else None,
    redoc_url="/redoc" if ENV != "production" else None,
)

app.add_middleware(BaseHTTPMiddleware, dispatch=api_key_middleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=len(ALLOWED_ORIGINS) > 0 and ALLOWED_ORIGINS[0] != "*",
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-API-Key"],
)

if ENV == "production":
    allowed_hosts = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost").split(",")]
    allowed_hosts += ["localhost", "127.0.0.1"]   # always allow local healthcheck
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    error_id = str(uuid.uuid4())[:8]
    logger.error("Unhandled error [%s] %s %s: %s",
                 error_id, request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error_id": error_id},
    )


PREFIX = "/api/v1"
app.include_router(leads_router,        prefix=PREFIX)
app.include_router(campaigns_router,    prefix=PREFIX)
app.include_router(inbox_router,        prefix=PREFIX)
app.include_router(pain_signals_router, prefix=PREFIX)
app.include_router(activity_router,     prefix=PREFIX)


@app.get("/health", tags=["system"])
async def health():
    db_ok = await check_db_health()
    return {
        "status":   "ok" if db_ok else "degraded",
        "database": "connected" if db_ok else "unreachable",
        "env":      ENV,
    }


@app.get("/", include_in_schema=False)
async def root():
    return {"service": "lead-engine-api", "version": "1.0.0"}
