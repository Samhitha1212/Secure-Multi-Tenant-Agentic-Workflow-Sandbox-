"""HTTP API for secure question-based workflow execution."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Mapping
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from secure_agentic_sandbox import runner
from secure_agentic_sandbox.auth_store import AuthStore
from secure_agentic_sandbox.checkpoint_store import (
    CheckpointConflictError,
    CheckpointNotFoundError,
    CheckpointStore,
)
from secure_agentic_sandbox.declarative_workflow import WorkflowSpecError
from secure_agentic_sandbox.question_registry import (
    QuestionNotFoundError,
    QuestionRegistry,
    create_default_question_registry,
)
from secure_agentic_sandbox.tool_broker import (
    ToolBroker,
    create_mock_tool_broker,
    validate_identifier,
)
from secure_agentic_sandbox.workflow_service import (
    WorkflowAuthorizationError,
    WorkflowExecutionService,
)


@dataclass(frozen=True, slots=True)
class ApiPrincipal:
    """Trusted identity and tool permissions."""

    tenant_id: str
    allowed_tools: frozenset[str]
    user_id: str | None = None
    email: str | None = None
    role: str | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.tenant_id, "tenant ID")

        if isinstance(self.allowed_tools, (str, bytes)):
            raise TypeError("Allowed tools must be a collection of names.")

        tools = frozenset(self.allowed_tools)

        if any(not isinstance(tool, str) or not tool for tool in tools):
            raise ValueError("Allowed tool names must be non-empty strings.")

        object.__setattr__(self, "allowed_tools", tools)

        identity_fields = (self.user_id, self.email, self.role)

        if any(value is not None for value in identity_fields) and not all(
            isinstance(value, str) and value.strip()
            for value in identity_fields
        ):
            raise ValueError(
                "User ID, email and role must all be provided."
            )


class WorkflowSubmission(BaseModel):
    """Request body for direct workflow execution."""

    model_config = ConfigDict(extra="forbid")

    specification: dict[str, object]
    execution_id: str | None = None


class QuestionSubmission(BaseModel):
    """Request body for submitting a solution to a registered question."""

    model_config = ConfigDict(extra="forbid")

    source: str | None = None
    specification: dict[str, object] | None = None
    execution_id: str | None = None

class DemoLoginRequest(BaseModel):
    """Request body for local demo authentication."""

    model_config = ConfigDict(extra="forbid")

    email: str


def load_api_keys_from_environment() -> dict[str, ApiPrincipal]:
    """Load development API keys from SECURE_SANDBOX_API_KEYS."""

    raw_config = os.getenv("SECURE_SANDBOX_API_KEYS")

    if not raw_config:
        return {}

    try:
        config = json.loads(raw_config)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "SECURE_SANDBOX_API_KEYS must contain valid JSON."
        ) from exc

    if not isinstance(config, dict):
        raise TypeError("API key configuration must be a JSON object.")

    api_keys: dict[str, ApiPrincipal] = {}

    for api_key, principal_data in config.items():
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("API keys must be non-empty strings.")

        if not isinstance(principal_data, dict):
            raise TypeError(
                "Each API key must have an object configuration."
            )

        if set(principal_data) != {"tenant_id", "allowed_tools"}:
            raise ValueError(
                "Each API key requires tenant_id and allowed_tools."
            )

        allowed_tools = principal_data["allowed_tools"]

        if not isinstance(allowed_tools, list):
            raise TypeError("allowed_tools must be a JSON array.")

        api_keys[api_key] = ApiPrincipal(
            tenant_id=principal_data["tenant_id"],
            allowed_tools=frozenset(allowed_tools),
        )

    return api_keys


def create_app(
    *,
    api_keys: Mapping[str, ApiPrincipal] | None = None,
    broker: ToolBroker | None = None,
    question_registry: QuestionRegistry | None = None,
    checkpoint_db_path: str | Path | None = None,
    demo_auth_enabled: bool | None = None,
) -> FastAPI:
    """Create the API with trusted tenant, broker and question configuration."""

    configured_keys = (
        load_api_keys_from_environment()
        if api_keys is None
        else dict(api_keys)
    )

    # Store hashes rather than original API tokens.
    key_registry: dict[bytes, ApiPrincipal] = {}

    for token, principal in configured_keys.items():
        if not isinstance(token, str) or not token:
            raise ValueError("API keys must be non-empty strings.")

        if not isinstance(principal, ApiPrincipal):
            raise TypeError("API key values must be ApiPrincipal objects.")

        digest = hashlib.sha256(token.encode("utf-8")).digest()
        key_registry[digest] = principal

    active_registry = (
        question_registry
        if question_registry is not None
        else create_default_question_registry()
    )

    database_path = (
        checkpoint_db_path
        if checkpoint_db_path is not None
        else os.getenv(
            "SECURE_SANDBOX_CHECKPOINT_DB",
            "data/workflow_checkpoints.sqlite3",
        )
    )

    if demo_auth_enabled is None:
        raw_demo_auth = os.getenv(
            "SECURE_SANDBOX_DEMO_AUTH_ENABLED",
            "false",
        ).strip().lower()

        if raw_demo_auth not in {"true", "false"}:
            raise RuntimeError(
                "SECURE_SANDBOX_DEMO_AUTH_ENABLED must be true or false."
            )

        demo_auth_enabled = raw_demo_auth == "true"

    auth_store = (
        AuthStore(database_path)
        if demo_auth_enabled
        else None
    )

    # The default mock inventory shares the checkpoint database so that
    # reservation results and stock changes survive application restarts.
    active_broker = (
        broker
        if broker is not None
        else create_mock_tool_broker(
            include_reservation=True,
            inventory_database_path=database_path,
        )
    )
    active_checkpoint_store = CheckpointStore(database_path)
    service = WorkflowExecutionService(
        active_broker,
        checkpoint_store=active_checkpoint_store,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            active_checkpoint_store.acquire_runtime_lock()
            active_checkpoint_store.recover_interrupted_executions()
            yield
        finally:
            active_checkpoint_store.close()

    app = FastAPI(
        title="Secure Agentic Workflow Sandbox",
        description=(
            "Submit Python and declarative workflow questions for "
            "isolated execution and authorized tool invocation."
        ),
        version="0.2.0",
        lifespan=lifespan,
    )

    app.state.broker = active_broker
    app.state.workflow_service = service
    app.state.question_registry = active_registry
    app.state.checkpoint_store = active_checkpoint_store

    def authenticate(authorization: str | None) -> ApiPrincipal:
        """Authenticate a bearer token and retrieve its trusted principal."""

        if authorization is None:
            raise HTTPException(
                status_code=401,
                detail="Bearer authentication required.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        parts = authorization.split()

        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(
                status_code=401,
                detail="Invalid authorization header.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        candidate = hashlib.sha256(parts[1].encode("utf-8")).digest()

        principal = None

        for stored_digest, stored_principal in key_registry.items():
            if hmac.compare_digest(candidate, stored_digest):
                principal = stored_principal

        if principal is None and auth_store is not None:
            user = auth_store.authenticate_session(parts[1])

            if user is not None:
                principal = ApiPrincipal(
                    tenant_id=user.tenant_id,
                    allowed_tools=user.allowed_tools,
                    user_id=user.user_id,
                    email=user.email,
                    role=user.role,
                )

        if principal is None:
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired authentication token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return principal

    def get_question(question_id: str):
        """Retrieve a question and translate registry errors into HTTP errors."""

        try:
            return active_registry.get(question_id)
        except QuestionNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail=str(exc),
            ) from exc

    def question_response(question, principal: ApiPrincipal) -> dict[str, object]:
        """Build a question response with effective tool permissions."""

        result = question.to_summary_dict()
        effective_tools = (
            question.allowed_tools
            & principal.allowed_tools
            & frozenset(active_broker.registered_tools)
        )

        result["required_tools"] = sorted(question.allowed_tools)
        result["available_tools"] = sorted(effective_tools)
        result["permissions_satisfied"] = (
            question.allowed_tools.issubset(effective_tools)
        )

        return result



    @app.get("/auth/demo-users")
    async def list_demo_users():
        """List identities available for local demo authentication."""

        if auth_store is None:
            raise HTTPException(
                status_code=404,
                detail="Demo authentication is disabled.",
            )

        return {"users": auth_store.list_demo_users()}

    @app.post("/auth/login")
    async def demo_login(request: DemoLoginRequest):
        """Create a demo session for a predefined user."""

        if auth_store is None:
            raise HTTPException(
                status_code=404,
                detail="Demo authentication is disabled.",
            )

        try:
            token, user = auth_store.create_session(request.email)
        except ValueError as exc:
            raise HTTPException(
                status_code=401,
                detail="Unknown or inactive demo user.",
            ) from exc

        return {
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "user_id": user.user_id,
                "email": user.email,
                "tenant_id": user.tenant_id,
                "role": user.role,
            },
        }

    @app.post("/auth/logout")
    async def demo_logout(
        authorization: str | None = Header(default=None),
    ):
        """Revoke the current demo session."""

        if auth_store is None:
            raise HTTPException(
                status_code=404,
                detail="Demo authentication is disabled.",
            )

        if authorization is None:
            raise HTTPException(
                status_code=401,
                detail="Bearer authentication required.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        parts = authorization.split()

        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(
                status_code=401,
                detail="Invalid authorization header.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = parts[1]

        if not auth_store.revoke_session(token):
            raise HTTPException(
                status_code=401,
                detail="Invalid, expired or already revoked demo session.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return {"status": "logged_out"}

    @app.get("/auth/me")
    async def get_current_user(
        authorization: str | None = Header(default=None),
    ):
        """Return the identity associated with the current bearer token."""

        principal = authenticate(authorization)

        if principal.user_id is None:
            raise HTTPException(
                status_code=403,
                detail="This endpoint requires demo-user authentication.",
            )

        return {
            "user_id": principal.user_id,
            "email": principal.email,
            "tenant_id": principal.tenant_id,
            "role": principal.role,
        }


    @app.get("/health")
    async def health():
        """Unauthenticated API health check."""

        return {"status": "healthy"}

    @app.get("/tools")
    async def list_tools(
        authorization: str | None = Header(default=None),
    ):
        """List tools available to the authenticated tenant."""

        principal = authenticate(authorization)

        available = (
            principal.allowed_tools
            & frozenset(active_broker.registered_tools)
        )

        return {"tools": sorted(available)}

    @app.get("/questions")
    async def list_questions(
        authorization: str | None = Header(default=None),
    ):
        """List questions available in the platform catalogue."""

        principal = authenticate(authorization)

        return {
            "questions": [
                question_response(question, principal)
                for question in active_registry.list_questions()
            ]
        }

    @app.get("/questions/{question_id}")
    async def question_details(
        question_id: str,
        authorization: str | None = Header(default=None),
    ):
        """Return the statement, requirements and starter submission."""

        principal = authenticate(authorization)
        question = get_question(question_id)

        result = question.to_detail_dict()
        result.update(question_response(question, principal))

        return result


    @app.post("/questions/{question_id}/submit")
    async def submit_question(
        question_id: str,
        request: QuestionSubmission,
        authorization: str | None = Header(default=None),
    ):
        """Execute a Python or workflow submission for a registered question."""

        principal = authenticate(authorization)
        question = get_question(question_id)

        if question.type == "python":
            if request.source is None or request.specification is not None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Python questions require 'source' and do not "
                        "accept 'specification'."
                    ),
                )

            # Python submissions retain server-generated execution IDs.
            execution_id = str(uuid4())

            try:
                execution_result = await runner.run_python_source(
                    request.source,
                    timeout_seconds=question.resource_config.timeout_seconds,
                    cpu=question.resource_config.cpu,
                    memory=question.resource_config.memory,
                    max_output_bytes=(
                        question.resource_config.max_output_bytes
                    ),
                )
            except (ValueError, OSError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail=str(exc),
                ) from exc

            if execution_result.oom_killed:
                status = "resource_exhausted"
            elif execution_result.timed_out:
                status = "timed_out"
            elif execution_result.exit_code == 0:
                status = "succeeded"
            else:
                status = "failed"

            return {
                "question_id": question.id,
                "execution_id": execution_id,
                "submission_type": "python",
                "status": status,
                "execution_result": asdict(execution_result),
            }

        if request.specification is None or request.source is not None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Workflow questions require 'specification' and do not "
                    "accept 'source'."
                ),
            )

        # Use the frontend-provided ID when available so that it can
        # request a pause while this workflow is executing.
        execution_id = request.execution_id or str(uuid4())

        try:
            validate_identifier(execution_id, "execution ID")
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Invalid execution ID.",
            ) from exc

        # Never let the submitted workflow choose its resource limits.
        # Apply the platform-defined limits to every Python step.
        specification = deepcopy(request.specification)
        steps = specification.get("steps")

        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict) and step.get("type") == "python":
                    step["cpu"] = question.resource_config.cpu
                    step["memory"] = question.resource_config.memory
                    step["timeout_seconds"] = (
                        question.resource_config.timeout_seconds
                    )
                    step["max_output_bytes"] = (
                        question.resource_config.max_output_bytes
                    )

        # Effective permissions are the intersection of:
        # question policy, tenant policy and registered tools.
        effective_tools = (
            question.allowed_tools
            & principal.allowed_tools
            & frozenset(active_broker.registered_tools)
        )

        try:
            result = await service.execute(
                tenant_id=principal.tenant_id,
                execution_id=execution_id,
                specification=specification,
                allowed_tools=effective_tools,
            )
        except WorkflowAuthorizationError as exc:
            raise HTTPException(
                status_code=403,
                detail=str(exc),
            ) from exc
        except WorkflowSpecError as exc:
            raise HTTPException(
                status_code=422,
                detail=str(exc),
            ) from exc

        return {
            "question_id": question.id,
            "execution_id": result.execution_id,
            "submission_type": "workflow",
            "status": result.status,
            "tools_used": list(result.tools_used),
            "step_results": result.step_results,
        }


    # Preserve the original direct workflow endpoint for development.
    @app.post("/workflows/execute")
    async def execute_workflow(
        request: WorkflowSubmission,
        authorization: str | None = Header(default=None),
    ):
        """Execute a workflow directly without selecting a question."""

        principal = authenticate(authorization)

        execution_id = request.execution_id or str(uuid4())
        try:
            validate_identifier(execution_id, "execution ID")
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="Invalid execution ID.",
            ) from exc

        try:
            result = await service.execute(
                tenant_id=principal.tenant_id,
                execution_id=execution_id,
                specification=request.specification,
                allowed_tools=principal.allowed_tools,
            )
        except WorkflowAuthorizationError as exc:
            raise HTTPException(
                status_code=403,
                detail=str(exc),
            ) from exc
        except WorkflowSpecError as exc:
            raise HTTPException(
                status_code=422,
                detail=str(exc),
            ) from exc

        return {
            "execution_id": result.execution_id,
            "status": result.status,
            "tools_used": list(result.tools_used),
            "step_results": result.step_results,
        }

    @app.post(
        "/executions/{execution_id}/pause",
        status_code=202,
    )
    async def pause_execution(
        execution_id: str,
        authorization: str | None = Header(default=None),
    ):
        """Request a safe pause for an execution owned by the caller."""

        principal = authenticate(authorization)

        try:
            checkpoint = active_checkpoint_store.request_pause(
                tenant_id=principal.tenant_id,
                execution_id=execution_id,
            )
        except CheckpointNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="Execution checkpoint not found.",
            ) from exc
        except CheckpointConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc

        return {
            "execution_id": execution_id,
            "status": "pause_requested",
            "checkpoint_status": checkpoint.status,
        }

    @app.post("/executions/{execution_id}/resume")
    async def resume_execution(
        execution_id: str,
        authorization: str | None = Header(default=None),
    ):
        """Resume an owned paused workflow from its saved checkpoint."""

        principal = authenticate(authorization)

        try:
            result = await service.resume(
                tenant_id=principal.tenant_id,
                execution_id=execution_id,
                allowed_tools=principal.allowed_tools,
            )
        except CheckpointNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="Execution checkpoint not found.",
            ) from exc
        except CheckpointConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc
        except WorkflowAuthorizationError as exc:
            raise HTTPException(
                status_code=403,
                detail=str(exc),
            ) from exc
        except WorkflowSpecError as exc:
            raise HTTPException(
                status_code=422,
                detail=str(exc),
            ) from exc

        return {
            "execution_id": result.execution_id,
            "status": result.status,
            "tools_used": list(result.tools_used),
            "step_results": result.step_results,
        }

    return app


app = create_app()
