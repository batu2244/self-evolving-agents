"""DeltaDesk API entrypoint.

The onboarding flow lives entirely in `app.onboarding` — the trading desk
(analysts, PM, evaluator) mounts its own routers here later without touching
that module.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.marketdata.router import router as marketdata_router
from app.onboarding.router import router as onboarding_router
from app.portfolio.router import router as portfolio_router

app = FastAPI(title="DeltaDesk API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(marketdata_router)
app.include_router(onboarding_router)
app.include_router(portfolio_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
