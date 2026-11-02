
"""FastAPI entry point. Wires routers only; no business logic lives here."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import agents, experiments, health, simulate
from app.api.ablation import router as ablation_router
from app.api.analysis import router as analysis_router
from app.api.metrics import router as metrics_router
from app.database.connection import init_db


# --------------------------------------------------
# Application lifespan
# --------------------------------------------------

@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize the database when the application starts."""
    init_db()
    yield


# --------------------------------------------------
# FastAPI application
# --------------------------------------------------

app = FastAPI(
    title="POSDCORB MAS Simulator API",
    version="0.1.0",
    lifespan=lifespan,
)


# --------------------------------------------------
# CORS configuration
# --------------------------------------------------

# Local development origins are allowed by default.
# Production origins are configured through Render
# environment variables.

allowed_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173"
).split(",")

allowed_origins = [
    origin.strip().rstrip("/")
    for origin in allowed_origins
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------
# API routers
# --------------------------------------------------

app.include_router(health.router, prefix="/api")
app.include_router(experiments.router, prefix="/api")
app.include_router(agents.router, prefix="/api")
app.include_router(simulate.router, prefix="/api")
app.include_router(metrics_router, prefix="/api")
app.include_router(analysis_router, prefix="/api")
app.include_router(ablation_router, prefix="/api")
