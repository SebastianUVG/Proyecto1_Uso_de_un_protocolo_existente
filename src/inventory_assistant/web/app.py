"""FastAPI adapter for the existing Inventory Assistant conversation runtime."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

import uvicorn
from fastapi import Cookie, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from inventory_assistant.chatbot.session import ChatbotError
from inventory_assistant.config import ConfigurationError, WebConfig
from inventory_assistant.llm import LLMProviderError
from inventory_assistant.mcp.client import MCPClientError

from .runtime import (
    BrowserSession,
    WebMessage,
    WebRuntime,
    WebRuntimeError,
    WebSessionNotFound,
    build_runtime_from_env,
    session_snapshot,
)


SESSION_COOKIE = "inventory_assistant_session"
STATIC_DIRECTORY = Path(__file__).with_name("static")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10_000)


def create_app(
    runtime_factory: Callable[[], WebRuntime] = build_runtime_from_env,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.runtime = None
        application.state.startup_error = None
        try:
            application.state.runtime = runtime_factory()
        except (ConfigurationError, LLMProviderError, MCPClientError, ValueError) as error:
            application.state.startup_error = _safe_startup_error(error)
        try:
            yield
        finally:
            runtime = application.state.runtime
            if runtime is not None:
                runtime.close()

    application = FastAPI(
        title="Inventory Assistant Web UI",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIRECTORY / "index.html")

    @application.post("/api/session")
    def ensure_session(
        response: Response,
        session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, Any]:
        runtime = _runtime(application)
        try:
            session = runtime.sessions.get(session_id)
        except WebSessionNotFound:
            session_id, session = runtime.sessions.create()
            _set_session_cookie(response, session_id)
        with session.lock:
            return session_snapshot(session)

    @application.post("/api/session/new")
    def new_session(
        response: Response,
        session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, Any]:
        runtime = _runtime(application)
        try:
            new_id, session = runtime.sessions.replace(session_id)
        except WebSessionNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        _set_session_cookie(response, new_id)
        return session_snapshot(session)

    @application.post("/api/chat")
    def chat(
        request: ChatRequest,
        session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, Any]:
        session = _browser_session(application, session_id)
        message = request.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="Message cannot be empty")
        with session.lock:
            if session.chatbot.pending_operation is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Confirm or cancel the pending operation first",
                )
            try:
                answer = session.chatbot.ask(message)
            except Exception as error:
                _raise_chat_error(error)
            session.messages.append(WebMessage("user", message))
            session.messages.append(WebMessage("assistant", answer))
            return session_snapshot(session)

    @application.post("/api/confirm")
    def confirm(
        session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, Any]:
        session = _browser_session(application, session_id)
        with session.lock:
            if session.chatbot.pending_operation is None:
                raise HTTPException(
                    status_code=409,
                    detail="There is no pending operation to confirm",
                )
            try:
                answer = session.chatbot.ask("yes")
            except Exception as error:
                _raise_chat_error(error)
            session.messages.append(WebMessage("action", "Operation confirmed"))
            session.messages.append(WebMessage("assistant", answer))
            return session_snapshot(session)

    @application.post("/api/cancel")
    def cancel(
        session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, Any]:
        session = _browser_session(application, session_id)
        with session.lock:
            if session.chatbot.pending_operation is None:
                raise HTTPException(
                    status_code=409,
                    detail="There is no pending operation to cancel",
                )
            try:
                answer = session.chatbot.ask("no")
            except Exception as error:
                _raise_chat_error(error)
            session.messages.append(WebMessage("action", "Operation cancelled"))
            session.messages.append(WebMessage("assistant", answer))
            return session_snapshot(session)

    @application.get("/api/status")
    def status(
        session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, Any]:
        _browser_session(application, session_id)
        runtime = _runtime(application)
        servers = runtime.server_statuses()
        return {
            "servers": servers,
            "connected": any(
                server["key"] == "inventory" and server["state"] == "Connected"
                for server in servers
            ),
        }

    @application.get("/api/logs")
    def logs(
        limit: int = Query(default=50, ge=1, le=200),
        session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> dict[str, Any]:
        _browser_session(application, session_id)
        try:
            records = _runtime(application).recent_logs(limit)
        except WebRuntimeError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        return {"logs": records}

    application.mount(
        "/static",
        StaticFiles(directory=STATIC_DIRECTORY),
        name="static",
    )
    return application


def _runtime(application: FastAPI) -> WebRuntime:
    runtime = application.state.runtime
    if runtime is None:
        raise HTTPException(
            status_code=503,
            detail=application.state.startup_error or "Web runtime is unavailable",
        )
    return runtime


def _browser_session(
    application: FastAPI,
    session_id: str | None,
) -> BrowserSession:
    try:
        return _runtime(application).sessions.get(session_id)
    except WebSessionNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _raise_chat_error(error: Exception) -> None:
    if isinstance(error, LLMProviderError):
        raise HTTPException(status_code=502, detail=str(error)) from error
    if isinstance(error, ChatbotError):
        raise HTTPException(status_code=502, detail=str(error)) from error
    if isinstance(error, MCPClientError):
        raise HTTPException(
            status_code=502,
            detail="Unable to reach a configured MCP server",
        ) from error
    raise error


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=8 * 60 * 60,
        httponly=True,
        samesite="strict",
        secure=False,
    )


def _safe_startup_error(error: Exception) -> str:
    if isinstance(error, ConfigurationError):
        return str(error)
    return "Unable to initialize the Inventory Assistant runtime"


app = create_app()


def main() -> None:
    config = WebConfig.from_env()
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
