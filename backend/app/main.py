"""FastAPI entry point. Wires routers only; no business logic lives here."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import agents, experiments, health, simulate
from app.api.ablation import router as ablation_router
from app.api.analysis import router as analysis_router
from app.api.metrics import router as metrics_router
from app.database.connection import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="POSDCORB MAS Simulator API",
    version="0.1.0",
    lifespan=lifespan,
)

# Dev-only CORS (Vite dev server). The Vite proxy normally makes this unnecessary.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(experiments.router, prefix="/api")
app.include_router(agents.router, prefix="/api")
app.include_router(simulate.router, prefix="/api")
app.include_router(metrics_router, prefix="/api")
app.include_router(analysis_router, prefix="/api")
app.include_router(ablation_router, prefix="/api")
