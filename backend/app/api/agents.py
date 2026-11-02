"""API router for agent pool management.

Endpoints:
  GET    /api/agents            → list all agents
  POST   /api/agents            → create an agent
  GET    /api/agents/{id}       → get one agent
  PATCH  /api/agents/{id}       → update agent config
  DELETE /api/agents/{id}       → remove agent from pool

Simulation runtime updates (status, cpu_usage, etc.) will be performed
by the simulation engine directly; these endpoints are for pool configuration.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.database.connection import get_connection
from app.models.agent import (
    Agent,
    AgentListResponse,
    AgentResponse,
    CreateAgentRequest,
    UpdateAgentRequest,
)

router = APIRouter(tags=["agents"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_agent(row) -> Agent:
    """Deserialise a DB row into an Agent model."""
    data = json.loads(row["agent_json"])
    return Agent(**data)


def _agent_to_dict(agent: Agent) -> dict:
    return json.loads(agent.model_dump_json())


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/agents", response_model=AgentListResponse)
def list_agents() -> AgentListResponse:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT agent_json FROM agents ORDER BY created_at ASC"
        ).fetchall()
    finally:
        conn.close()

    agents = [_row_to_agent(r) for r in rows]
    initial = sum(1 for a in agents if a.origin == "initial")
    return AgentListResponse(
        agents=agents,
        total=len(agents),
        initial_count=initial,
        dynamic_count=len(agents) - initial,
    )


@router.post("/agents", response_model=AgentResponse, status_code=201)
def create_agent(body: CreateAgentRequest) -> AgentResponse:
    agent_id = f"agt-{uuid.uuid4().hex[:8]}"
    created_at = datetime.now(timezone.utc).isoformat()
    agent = Agent(
        id=agent_id,
        name=body.name,
        role=body.role,
        capabilities=body.capabilities,
        processing_speed=body.processing_speed,
        reliability=body.reliability,
        max_concurrent_tasks=body.max_concurrent_tasks,
        origin=body.origin,
        created_at=created_at,
    )
    agent_json = agent.model_dump_json()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO agents (id, name, role, origin, agent_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (agent.id, agent.name, agent.role, agent.origin, agent_json, created_at),
        )
        conn.commit()
    finally:
        conn.close()

    return AgentResponse(
        agent=agent,
        message=f"Agent '{agent.name}' ({agent.role}) created with id {agent.id}.",
    )


@router.get("/agents/{agent_id}", response_model=Agent)
def get_agent(agent_id: str) -> Agent:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT agent_json FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")
    return _row_to_agent(row)


@router.patch("/agents/{agent_id}", response_model=AgentResponse)
def update_agent(agent_id: str, body: UpdateAgentRequest) -> AgentResponse:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT agent_json FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")

        agent = _row_to_agent(row)
        update_data = body.model_dump(exclude_none=True)
        updated = agent.model_copy(update=update_data)
        agent_json = updated.model_dump_json()

        conn.execute(
            "UPDATE agents SET name=?, role=?, origin=?, agent_json=? WHERE id=?",
            (updated.name, updated.role, updated.origin, agent_json, agent_id),
        )
        conn.commit()
    finally:
        conn.close()

    return AgentResponse(agent=updated, message=f"Agent '{agent_id}' updated.")


@router.delete("/agents/{agent_id}", status_code=204)
def delete_agent(agent_id: str) -> None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM agents WHERE id = ?", (agent_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")
        conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        conn.commit()
    finally:
        conn.close()
