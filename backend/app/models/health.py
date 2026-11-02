from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    timestamp: float
    python_version: str
    database: Literal["ok", "error"]
    cpu_count: int
    memory_total_mb: int
