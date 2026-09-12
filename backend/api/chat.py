"""Chat API: start an execution, stream its events.

``POST /api/chat`` returns immediately with an ``execution_id``; the run itself
happens in a background task and reports through SSE. That is what keeps the UI
responsive from the first millisecond instead of blocking for the whole run.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from api.container import AppContainer
from api.deps import get_container
from models.schemas import ChatRequest, ChatResponse, Conversation, Execution, Message
from services.event_bus import ExecutionChannel

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

#: How long an idle SSE connection may stay silent before we emit a heartbeat.
_HEARTBEAT_S = 15.0


@router.post("/chat", response_model=ChatResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_chat(
    payload: ChatRequest,
    container: AppContainer = Depends(get_container),
) -> ChatResponse:
    """Persist the user message and launch the orchestration run."""
    conversation = await container.database.ensure_conversation(payload.conversation_id)

    user_message_id = await container.database.add_message(
        conversation.id, "user", payload.message
    )

    if not container.settings.deepseek_configured:
        # Fail with an actionable message instead of a 500 from deep inside a task.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "DeepSeek is not configured. Set DEEPSEEK_API_KEY in the project .env file "
                "and restart the backend."
            ),
        )

    try:
        execution = await container.orchestrator.start(
            conversation_id=conversation.id,
            user_input=payload.message,
            user_message_id=user_message_id,
            max_steps=payload.max_steps,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("failed to start execution")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not start the execution: {exc}",
        ) from exc

    return ChatResponse(
        execution_id=execution.id,
        conversation_id=conversation.id,
        user_message_id=user_message_id,
        status="running",
        stream_url=f"/api/events/{execution.id}",
    )


@router.post("/executions/{execution_id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_execution(
    execution_id: str,
    container: AppContainer = Depends(get_container),
) -> dict[str, object]:
    cancelled = await container.orchestrator.cancel(execution_id)
    return {"execution_id": execution_id, "cancelled": cancelled}


@router.get("/events/{execution_id}")
async def stream_events(
    execution_id: str,
    request: Request,
    container: AppContainer = Depends(get_container),
) -> StreamingResponse:
    """Server-Sent Events for one execution.

    Replays the backlog first, so a browser that connects after the run started
    still receives every event, then streams live until a terminal event.
    """
    channel: ExecutionChannel = container.event_bus.channel(execution_id)

    async def event_source():
        try:
            async with channel.subscribe() as stream:
                iterator = stream.__aiter__()
                while True:
                    if await request.is_disconnected():
                        log.debug("sse_client_disconnected execution=%s", execution_id)
                        return
                    try:
                        event = await asyncio.wait_for(iterator.__anext__(), timeout=_HEARTBEAT_S)
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
                        continue
                    except StopAsyncIteration:
                        return
                    yield _frame(event.type, json.loads(event.model_dump_json(exclude_none=True)))
        except asyncio.CancelledError:  # pragma: no cover - client went away
            return

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_source(), media_type="text/event-stream", headers=headers)


@router.get("/conversations", response_model=list[Conversation])
async def list_conversations(
    limit: int = 100,
    container: AppContainer = Depends(get_container),
) -> list[Conversation]:
    return await container.database.list_conversations(limit=min(max(limit, 1), 500))


@router.get("/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(
    conversation_id: str,
    container: AppContainer = Depends(get_container),
) -> Conversation:
    conversation = await container.database.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


@router.get("/conversations/{conversation_id}/messages", response_model=list[Message])
async def list_messages(
    conversation_id: str,
    container: AppContainer = Depends(get_container),
) -> list[Message]:
    return await container.database.list_messages(conversation_id)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_200_OK)
async def delete_conversation(
    conversation_id: str,
    container: AppContainer = Depends(get_container),
) -> dict[str, object]:
    deleted = await container.database.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return {"deleted": True, "conversation_id": conversation_id}


@router.get("/conversations/{conversation_id}/executions", response_model=list[Execution])
async def list_conversation_executions(
    conversation_id: str,
    limit: int = 50,
    container: AppContainer = Depends(get_container),
) -> list[Execution]:
    return await container.database.list_executions(conversation_id, limit=min(max(limit, 1), 200))


def _frame(event_type: str, payload: dict) -> str:
    """Encode one SSE frame. ``event:`` lets EventSource dispatch by type."""
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


__all__ = ["router"]
