# POSDCORB MAS Simulator

Research dashboard for the paper **"Comparative Simulation of Conventional and POSDCORB-Based Managers in Multi-Agent Systems"**.

Two manager policies (Conventional LLM Supervisor vs POSDCORB Adaptive Manager) will be run against identical workers, tasks, hardware and workload. Only the manager policy differs.

> Status: the manager/engine, metrics, experiment-analysis and ablation modules are
> implemented. Some legacy dashboard pages still render illustrative mock data.

## Stack

- Frontend: React + TypeScript, Vite, Tailwind CSS 3, Recharts, Lucide React
- Backend: Python FastAPI, psutil, SQLite (stdlib `sqlite3`)
- Communication: REST/JSON. No auth, no Docker, no cloud.

## Structure

```
frontend/src/
  components/  layouts/  pages/  services/  types/  hooks/  data/
backend/app/
  api/         REST routers (thin; no business logic)
  managers/    Conventional + POSDCORB manager policies
  agents/      Shared worker agents
  simulation/  Engine, tasks, workload
  metrics/     psutil + perf counters
  ablation/    POSDCORB ablation ladder, runner and fallback agent pool
  database/    SQLite connection and schema
  models/      Pydantic models (mirror frontend/src/types)
backend/data/  SQLite file lives here (git-ignored)
```

`frontend/src/types/experiment.ts` and `backend/app/models/experiment.py` must be kept in sync.

## Prerequisites

- Node.js 18+ (20+ recommended)
- Python 3.10+

## Run the backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Check it: <http://127.0.0.1:8000/api/health> (interactive docs at `/docs`).

## Run the frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. Vite proxies `/api` to `http://127.0.0.1:8000`, so no CORS setup is needed. To point at a different backend, set `VITE_API_BASE_URL` in `frontend/.env`.

The dashboard shows live backend/database status from `GET /api/health`.

## Production build check

```bash
cd frontend && npm run build
```

## Dashboard shell

Ten pages behind a persistent sidebar: Dashboard, New Experiment, Agent Pool, Workload, Simulation, Results, Comparison, Experiment Analysis, Ablation Study, Experiment History.

- Routing is a tiny hash router (`hooks/useHashRoute.ts`, routes like `#/results/11`), so there is no router dependency.
- Legacy dashboard/history/result views still read from `frontend/src/data/mock.ts`, which is **illustrative, not experimental results**. The live views (Simulation, Experiment Analysis, Ablation Study) call the backend through `services/api.ts`.
- Aggregation lives in `services/selectors.ts`, so pages stay presentational.
- Reusable components in `components/`: `MetricCard`, `SectionHeader`, `StatusBadge`, `ComparisonCard`, `DataTable` (sortable, generic), `ChartCard`, plus `Tabs`, `BackendStatus` and shared Recharts styling in `chartStyle.ts`.
- New Experiment validates and previews the config but cannot submit; Simulation shows layout only with disabled controls.

## POSDCORB ablation study

Answers *which POSDCORB mechanisms contribute most to performance* by running nine
cumulative manager configurations against one identical workload.

| # | Configuration | Enabled principles | Adaptive layer |
|---|---------------|--------------------|----------------|
| 1 | Conventional Manager | none (baseline) | n/a |
| 2 | Planning only | P | off |
| 3 | Planning + Organizing | P, O | off |
| 4 | + Staffing | P, O, S | off |
| 5 | + Directing | P, O, S, D | off |
| 6 | + Coordinating | P, O, S, D, CO | off |
| 7 | + Reporting | P, O, S, D, CO, R | off |
| 8 | + Budgeting | P, O, S, D, CO, R, B | off |
| 9 | Full POSDCORB | all seven | on |

Rungs 2–8 are cumulative and keep the cross-cutting adaptive layer **off**, so each
marginal delta isolates exactly one principle. Rung 9 re-enables adaptive
replanning, failure recovery and deadlock detection.

### Reported metrics

`latency_ms`, `throughput_tasks_per_sec`, `success_rate`, `cpu_utilization`,
`coordination_overhead`, `agent_utilization`, `activation_count`.

Improvements are **directional**: a positive percentage always means *better than
the Conventional baseline*, regardless of whether the metric prefers higher
(throughput, success, utilisation) or lower (latency, overhead) values.
`activation_count` has no inherent direction, so it is reported as a signed delta
and excluded from the composite score. The composite is the mean directional
improvement across the other six metrics.

### Fairness contract

Every configuration receives the same generated task graph (per repetition seed),
the same agent pool, and the same resource envelope. Nothing is allowed to vary
except the manager strategy and the enabled principles. `conditions.fingerprint`
hashes the workload, agent-pool signature and limits so a report can be audited;
`conditions.identical_inputs` records that the guarantee held.

Without saved agents, the study uses a deterministic 4 initial + 4 dynamic pool
(`backend/app/ablation/defaults.py`) that covers all seven task capabilities, so it
runs on a fresh checkout.

### Endpoints

```bash
# Describe the ladder and metric definitions (no simulation)
curl http://127.0.0.1:8000/api/ablation/configurations

# Run the study
curl -X POST http://127.0.0.1:8000/api/ablation/study \
  -H 'Content-Type: application/json' \
  -d '{"workload":{"num_tasks":20,"complexity":"medium","dependency_percentage":25.0},
       "repetitions":1,"base_seed":42,"max_decisions":5000,
       "max_wall_ms":120000,"failure_probability":0.0}'
```

The response contains the comparison table, per-metric `{value, mean, std, min,
max, n}`, `improvements_vs_baseline`, `marginal_vs_previous`,
`principle_contributions`, a `ranking`, raw per-run records, and
`mechanism_activity` (mean event counts proving each mechanism actually ran).

### Enabling/disabling principles in your own code

```python
from app.managers.posdcorb import POSDCORBConfig, POSDCORBManager

manager = POSDCORBManager(config=POSDCORBConfig(
    enabled_principles=frozenset({"planning", "staffing"}),
    adaptive_mechanisms=False,
))
```

Canonical keys live in `backend/app/managers/principles.py`. Unknown keys raise
`ValueError`, so typos cannot silently disable a mechanism.

### UI

`#/ablation` (sidebar: *Ablation Study*) exposes the shared conditions, the
comparison table, per-metric bar charts, the directional-improvement chart/table,
the marginal-contribution ranking, and the raw run records. A 0.0% contribution
means the mechanism ran but did not change the measured outcome under those
conditions — the *Active mechanisms* column shows which trace events fired, and
changing task count, dependencies or failure probability probes other regimes.

