import platform
import time

from fastapi import APIRouter

from app.database.connection import check_db
from app.models.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    # psutil is an optional dependency (host monitoring).  The API must stay
    # importable and healthy without it, so report zeros instead of crashing.
    try:
        import psutil  # type: ignore[import]
    except ImportError:
        cpu_count = 0
        memory_total_mb = 0
    else:
        cpu_count = psutil.cpu_count(logical=True) or 0
        memory_total_mb = round(psutil.virtual_memory().total / (1024 * 1024))

    return HealthResponse(
        status="ok",
        version="0.1.0",
        timestamp=time.time(),
        python_version=platform.python_version(),
        database="ok" if check_db() else "error",
        cpu_count=cpu_count,
        memory_total_mb=memory_total_mb,
    )
