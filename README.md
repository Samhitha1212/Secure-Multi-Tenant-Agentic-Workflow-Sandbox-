# Secure Multi-Tenant Agentic Workflow Sandbox

A single-machine prototype for securely executing untrusted Python programs and agentic workflows across multiple tenants. The system combines isolated execution, controlled tool access, persistent workflow checkpoints, pause/resume, and tenant-scoped authorization.

## 1. Project Overview

Agentic workflows may execute user-provided Python code and interact with external tools. Running these workloads without isolation can expose the host system, other tenants' data, and sensitive resources to malicious or faulty code.

This project explores a sandboxed execution platform that provides:

- Isolated Python execution using OpenSandbox, Docker, and gVisor.
- Support for Python programs and LangGraph-based workflows.
- Tenant-scoped access control and tool authorization.
- A mock inventory service for controlled tool interactions.
- Persistent workflow checkpoints using SQLite.
- Pause and resume from the first unfinished workflow step.
- Resource limits and execution output handling.
- Automated tests for execution, authorization, isolation, and recovery.

This is a research and academic prototype, not a production-ready multi-tenant cloud platform.

## 2. Architecture

The platform consists of the following major components:

| Component | Responsibility |
|---|---|
| Frontend | Demo user selection, exercise selection, code/workflow submission, and execution output |
| FastAPI backend | Authentication, request validation, authorization, and execution coordination |
| OpenSandbox | Creates and manages isolated execution environments |
| Docker + gVisor | Provides container-based execution with an additional isolation boundary |
| Workflow service | Executes workflow steps and manages pause/resume |
| Tool broker | Controls access to permitted tools |
| Mock inventory service | Provides local inventory lookup and reservation operations |
| SQLite checkpoint store | Persists workflow state, results, and execution progress |
| Auth store | Stores demo users and session information |

### Execution flow

1. A user selects a demo account and an exercise.
2. The frontend submits the program or workflow to the FastAPI backend.
3. The backend authenticates the user and checks tenant and tool permissions.
4. Python code is executed through OpenSandbox using the configured gVisor runtime.
5. Workflow tool calls are routed through the tool broker.
6. Workflow progress is persisted in SQLite.
7. A workflow can be paused at a step boundary and resumed from its checkpoint.
8. Execution results are returned to the frontend.

## 3. Technology Stack

- **Backend:** Python 3.13+, FastAPI, Uvicorn
- **Execution isolation:** OpenSandbox, Docker, gVisor (`runsc`)
- **Workflow execution:** LangGraph
- **Persistence:** SQLite
- **Frontend:** React, TypeScript, Vite
- **Testing and quality:** pytest, Ruff, ESLint, TypeScript

## 4. Prerequisites

The development setup assumes Ubuntu/Linux and requires:

- Python 3.13 or later
- Docker
- gVisor (`runsc`) installed and registered as a Docker runtime
- OpenSandbox server tooling through `uv`
- Node.js and npm
- `curl`, `ip`, `iptables`, `python3`, and `pgrep`
- `sudo` access for the development network firewall setup

The startup script expects:

- `~/.local/bin/uv`
- `~/.sandbox.toml`
- Docker to be accessible without `sudo`

The OpenSandbox configuration must use the Docker runtime, the configured internal network, and gVisor as the secure runtime. The script validates these settings before starting the server.

## 5. Setup

### Clone the repository

```bash
git clone <repository-url>
cd Secure-Multi-Tenant-Agentic-Workflow-Sandbox-
```

### Set up the Python environment

Install the project and development dependencies:

```bash
uv sync --dev
```

Activate the environment if you prefer using its commands directly:

```bash
source .venv/bin/activate
```

### Set up the frontend

```bash
cd frontend
npm install
cd ..
```

## 6. Running the Application

The development setup uses three separate processes. Start each in a separate terminal.

### Terminal 1: OpenSandbox server

From the project root:

```bash
./scripts/start_dev.sh
```

The script:

- Checks the required system commands and OpenSandbox configuration.
- Verifies Docker access and the gVisor runtime configuration.
- Creates or reuses the configured internal Docker network.
- Applies the host firewall rule used by the development network.
- Starts the OpenSandbox server at `http://127.0.0.1:8080`.

Keep this terminal running.

**Important:** The script starts only OpenSandbox. It does not start FastAPI or the frontend. The Docker network and firewall rule are not automatically removed when the script is stopped.

### Terminal 2: FastAPI backend

From the project root:

```bash
export SECURE_SANDBOX_DEMO_AUTH_ENABLED=true

uv run uvicorn secure_agentic_sandbox.api:app \
    --host 127.0.0.1 \
    --port 8000
```

The backend is available at:

- API: `http://127.0.0.1:8000`
- Health check: `http://127.0.0.1:8000/health`
- API documentation: `http://127.0.0.1:8000/docs`

Demo authentication must be enabled for the frontend's demo account selector to work. It is disabled by default.

### Terminal 3: Frontend

```bash
cd frontend
npm run dev
```

Open:

`http://127.0.0.1:5173`

The Vite development server forwards `/api` requests to the backend at `http://127.0.0.1:8000`.

### Stopping the application

Press `Ctrl+C` in each terminal to stop the corresponding process.

Stopping OpenSandbox does not automatically remove the development Docker network or the firewall rule created by the startup script.

## 7. Demo Accounts and Authorization

The local demo authentication system provides three predefined accounts.

| Demo account | Tenant | Permitted tools |
|---|---|---|
| `alice@tenant-a.demo` | tenant-a | `inventory.lookup`, `inventory.reserve` |
| `charlie@tenant-a.demo` | tenant-a | `inventory.lookup` |
| `bob@tenant-b.demo` | tenant-b | `inventory.lookup` |

Select an account from the frontend to create a demo session.

The demo login is based on selecting a predefined email address. It does not verify a real identity or password and must not be used as production authentication.

### Authorization behaviour

- Users belonging to the same tenant can access that tenant's workflow checkpoints, subject to the implemented authorization rules.
- Users from another tenant cannot access those checkpoints.
- Tool permissions are checked by the backend.
- A user without `inventory.reserve` permission cannot execute a workflow requiring that tool.
- Exercise visibility does not imply permission to execute every exercise.

## 8. Configuration

The following environment variables are supported:

| Variable | Purpose | Default |
|---|---|---|
| `SECURE_SANDBOX_DEMO_AUTH_ENABLED` | Enables local demo authentication | `false` |
| `SECURE_SANDBOX_CHECKPOINT_DB` | SQLite database path for application persistence | `data/workflow_checkpoints.sqlite3` |
| `SECURE_SANDBOX_API_KEYS` | Optional legacy API-key configuration in JSON format | Unset |

Example:

```bash
export SECURE_SANDBOX_DEMO_AUTH_ENABLED=true
```

The checkpoint database is also used by the default mock inventory service and demo authentication store. Keep the database file if you want to retain local checkpoints and inventory state between restarts.

## 9. Workflow Pause and Resume

The workflow service persists execution progress in SQLite.

When a pause is requested:

1. The currently running step is allowed to finish.
2. Its result is checkpointed.
3. The workflow transitions to a paused state.
4. The execution sandbox is cleaned up.

When the workflow is resumed:

1. The checkpoint is retrieved using tenant and execution identifiers.
2. Previously completed steps are not replayed.
3. Execution continues from the first unfinished step.
4. A fresh sandbox and tool authorization context are created.

The implementation also includes recovery of interrupted workflow checkpoints when the API restarts.

## 10. Testing

### Backend tests

From the project root:

```bash
ruff check .
python -m pytest -q
```

### Frontend checks

```bash
cd frontend
npm run lint
npm run build
```

### Live M4 integration tests

These tests require Docker, gVisor, a running OpenSandbox server, and the local `python:3.11` execution image.

Run the live pause/resume test:

```bash
uv run python tests/integration/m4_live_pause_resume.py
```

Run the API crash-recovery test:

```bash
uv run python tests/integration/m4_api_crash_recovery.py
```

The API crash-recovery test manually removes the interrupted container after terminating the API process. This is an important limitation: automatic cleanup of every orphaned container after an abrupt API termination is not established by that test.

See `tests/README.md` and the milestone test records for further details.

## 11. Repository Structure

```text
.
├── frontend/
│   ├── src/
│   ├── package.json
│   └── vite.config.ts
├── scripts/
│   ├── start_dev.sh
│   └── sdk_smoke_test.py
├── src/
│   └── secure_agentic_sandbox/
│       ├── api.py
│       ├── auth_store.py
│       ├── checkpoint_store.py
│       ├── tool_broker.py
│       └── ...
├── tests/
│   ├── integration/
│   │   ├── m4_api_crash_recovery.py
│   │   └── m4_live_pause_resume.py
│   ├── unit/
│   ├── evidence/
│   ├── M1_TEST_RECORD.md
│   ├── M2_TEST_RECORD.md
│   ├── M3_TEST_RECORD.md
│   ├── M4_TEST_RECORD.md
│   └── README.md
├── pyproject.toml
└── README.md
```

## 12. Security Scope and Limitations

This project is a single-machine prototype intended for controlled academic experimentation.

- Execution uses Docker and gVisor, but the complete platform has not undergone a production security audit.
- Demo authentication is not real identity verification.
- The inventory service uses local mock data rather than a real external service.
- The prototype uses local configuration and dummy data; it is not intended to handle production secrets.
- Network restrictions depend on the configured Docker network and host firewall.
- Abrupt API termination can leave an orphaned sandbox container requiring manual cleanup.
- The system does not yet implement a distributed multi-host execution platform.

The implementation should therefore be evaluated as a prototype demonstrating isolation, tenant-scoped authorization, stateful workflow execution, and recovery mechanisms.