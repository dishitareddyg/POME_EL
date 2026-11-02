import type {
  CreateExperimentRequest,
  CreateExperimentResponse,
  HealthResponse,
} from "../types/experiment";
import type { CompareRequest, CompareResponse } from "../types/metrics";
import type { BatchRequest, BatchResponse } from "../types/analysis";
import type {
  AblationConfigurationsResponse,
  AblationStudyRequest,
  AblationStudyResponse,
} from "../types/ablation";
import type {
  SessionCreateRequest,
  SessionCreateResponse,
  SessionState,
  SimulateRequest,
  SimulateResponse,
} from "../types/simulation";

const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    // Try to surface backend detail message
    let detail = `API ${res.status} ${res.statusText} on ${path}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // ignore parse error
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const api = {
  getHealth: () => request<HealthResponse>("/health"),

  createExperiment: (payload: CreateExperimentRequest) =>
    request<CreateExperimentResponse>("/experiments", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /** POST /api/simulate — run one or more managers synchronously. */
  simulate: (payload: SimulateRequest) =>
    request<SimulateResponse>("/simulate", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /** POST /api/simulate/session — create a step-wise session. */
  createSession: (payload: SessionCreateRequest) =>
    request<SessionCreateResponse>("/simulate/session", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /** GET /api/simulate/status/{session_id} — poll session state. */
  getSessionStatus: (sessionId: string) =>
    request<SessionState>(`/simulate/status/${sessionId}`),

  /** POST /api/simulate/status/{session_id}/step — advance one manager. */
  stepSession: (sessionId: string) =>
    request<{ stepped: boolean; state: SessionState }>(
      `/simulate/status/${sessionId}/step`,
      { method: "POST" }
    ),

  /** DELETE /api/simulate/status/{session_id} — stop a session early. */
  stopSession: (sessionId: string) =>
    request<{ stopped: boolean; state: SessionState }>(
      `/simulate/status/${sessionId}`,
      { method: "DELETE" }
    ),

  /**
   * POST /api/metrics/compare — compare Conventional vs POSDCORB metrics.
   * The ``experiments`` dict must contain both 'conventional' and 'posdcorb' keys
   * mapped to ExperimentResult summary_dict() objects from simulate.experiments.
   */
  compareMetrics: (payload: CompareRequest) =>
    request<CompareResponse>("/metrics/compare", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /**
   * POST /api/analysis/batch — run a parameter sweep comparing both managers.
   * Returns raw runs, per-config statistics, effect sizes, and chart data.
   */
  runBatch: (payload: BatchRequest) =>
    request<BatchResponse>("/analysis/batch", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /**
   * POST /api/ablation/study — run the nine cumulative POSDCORB
   * configurations against one identical workload.
   */
  runAblation: (payload: AblationStudyRequest) =>
    request<AblationStudyResponse>("/ablation/study", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /** GET /api/ablation/configurations — describe the ladder + metrics. */
  getAblationConfigurations: () =>
    request<AblationConfigurationsResponse>("/ablation/configurations"),
};
