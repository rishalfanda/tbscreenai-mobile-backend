from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.api.router import api_router
from app.core.config import get_settings
from app.core.rate_limit import limiter, rate_limit_exceeded_handler

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Shared backend for TBScreenAI (roles: doctor, admin_rs, super_admin). "
        "All medical data is tenant-scoped per hospital. "
        "AI inference is MOCKED in this phase."
    ),
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
