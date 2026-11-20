# Frontend

React + TypeScript + Vite frontend for the Secure Multi-Tenant Agentic Workflow Sandbox.

The interface allows users to select a demo account, browse available exercises, submit Python programs or workflows, inspect execution results, and pause or resume supported workflows.

## Prerequisites

- Node.js
- npm
- The FastAPI backend running at `http://127.0.0.1:8000`
- OpenSandbox running at `http://127.0.0.1:8080` for actual sandbox execution

## Installation

From the repository root:

```bash
cd frontend
npm install
```

## Development

Start the frontend development server:

```bash
npm run dev
```

Open:

`http://127.0.0.1:5173`

The Vite development server proxies requests beginning with `/api` to:

`http://127.0.0.1:8000`

The backend must be started separately. For demo account selection, enable demo authentication when starting the backend:

```bash
export SECURE_SANDBOX_DEMO_AUTH_ENABLED=true

uv run uvicorn secure_agentic_sandbox.api:app \
    --host 127.0.0.1 \
    --port 8000
```

OpenSandbox must also be running for real sandbox executions.

See the repository's root `README.md` for the complete startup procedure and system configuration.

## Demo Accounts

| Account | Tenant | Permissions |
|---|---|---|
| `alice@tenant-a.demo` | tenant-a | Inventory lookup and reservation |
| `charlie@tenant-a.demo` | tenant-a | Inventory lookup |
| `bob@tenant-b.demo` | tenant-b | Inventory lookup |

These are local demo accounts. Selecting an account does not verify a real identity.

## Available Scripts

### `npm run dev`

Starts the Vite development server.

### `npm run build`

Runs TypeScript project checks and creates a production build in `dist/`.

### `npm run lint`

Runs ESLint on the frontend source.

### `npm run preview`

Serves the production build locally for preview.

## Frontend Structure

```text
frontend/
├── src/
│   ├── App.tsx
│   ├── App.css
│   ├── index.css
│   └── ...
├── index.html
├── package.json
├── tsconfig.json
└── vite.config.ts
```

## Validation

Run the frontend checks:

```bash
npm run lint
npm run build
```