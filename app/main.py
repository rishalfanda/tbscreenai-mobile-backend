import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.api.router import api_router
from app.core.config import get_settings
from app.core.rate_limit import limiter, rate_limit_exceeded_handler
from app.services.storage import StorageError, get_object_storage

logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Make sure the image bucket exists before the first upload arrives.

    Warns rather than refuses to start, deliberately. Login, the patient list
    and sync do not touch object storage, and taking the whole API down because
    MinIO is thirty seconds slower to come up would trade a narrow outage for a
    total one. The first storage call still fails loudly and specifically if
    the problem is real, so nothing is silently swallowed — the warning here is
    an early heads-up, not the error handling.
    """
    if settings.storage_auto_create_bucket:
        try:
            get_object_storage().ensure_bucket()
        except StorageError:
            logger.warning(
                "Object storage is not reachable at startup; image endpoints "
                "will fail until it is. Bucket: %r",
                settings.storage_bucket,
                exc_info=True,
            )
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Shared backend for TBScreenAI (roles: doctor, admin_rs, super_admin). "
        "All medical data is tenant-scoped per hospital. "
        "AI inference is MOCKED in this phase."
    ),
    lifespan=lifespan,
)

# slowapi reads the limiter off app.state. The handler is ours rather than
# slowapi's — see app/core/rate_limit.py for why.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # No cookies — auth is via the Authorization header, so credentials stay
    # off (required anyway when allow_origins is "*").
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}
