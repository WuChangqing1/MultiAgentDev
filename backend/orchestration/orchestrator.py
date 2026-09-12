"""Orchestrator -- the runtime.

Owns the execution lifecycle end to end:

    conversation/message persistence -> MainAgent decision -> routing ->
    worker dispatch -> result feedback -> main reasoning -> final answer ->

and, throughout, the three separated data flows required by the project:

* **Context**     -> the task/context the model needs        -> prompt body
* **Agent State** -> flow state of the current execution     -> prompt TAIL
* **Telemetry**   -> tokens, latency, status, events         -> DB / SSE / UI

Telemetry is written to the database and published on the event bus in this
module. It is never handed to an agent, and the prompt builder could not accept
it if it were.

A worker failure never fails the task: the error is folded into agent state and
the MainAgent is asked again, so DeepSeek can absorb the work itself.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from agents.base import AgentError
from agents.main_agent import MainAgent, MainAgentTurn
from agents.reviewer import ReviewerAgent
from core.config import Settings
from core.logging_setup import execution_id_var
from db.database import Database
from models.events import ExecutionEvent
from models.schemas import (
    AgentFlowNode,
    AgentResult,
    AgentStep,
    AgentTask,
    Execution,
    Message,
)
from models.token_usage import TokenUsage, utcnow
from orchestration.context_manager import ContextManager
from orchestration.registry import MAIN_AGENT_KEY, AgentRegistry
from orchestration.router import Router
from orchestration.state_manager import ExecutionStateManager
from providers.base import ProviderError, ProviderUnavailable
from providers.llama_cpp import LlamaCppProvider
from services.event_bus import EventBus
from services.model_health import ModelHealthService
from services.runtime_state import RuntimeStateStore
from services.token_tracker import TokenTracker

log = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 12


@dataclass
class ExecutionOutcome:
    execution_id: str
    conversation_id: str
    status: str
    final_answer: str | None = None
    error: str | None = None
    steps: list[AgentStep] = field(default_factory=list)
    usage: TokenUsage | None = None
    notice: str | None = None


class Orchestrator:
    """Coordinates one execution at a time, several executions concurrently."""

    def __init__(
        self,
        *,
        settings: Settings,
        registry: AgentRegistry,
        database: Database,
        event_bus: EventBus,
        token_tracker: TokenTracker,
        runtime_state: RuntimeStateStore,
        model_health: ModelHealthService,
        local_provider: LlamaCppProvider,
        context_manager: ContextManager | None = None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._db = database
        self._bus = event_bus
        self._tokens = token_tracker
        self._runtime = runtime_state
        self._health = model_health
        self._local_provider = local_provider
        self._context = context_manager or ContextManager()
        self._tasks: dict[str, asyncio.Task[ExecutionOutcome]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def start(
        self,
        *,
        conversation_id: str,
        user_input: str,
        user_message_id: int,
        max_steps: int | None = None,
    ) -> Execution:
        """Create the execution record and launch the run in the background."""
        settings = self._settings
        execution = await self._db.create_execution(
            conversation_id, user_input, user_message_id=user_message_id
        )
        await self._runtime.mark_idle_agents()

        task = asyncio.create_task(
            self._run(
                execution=execution,
                user_input=user_input,
                max_steps=_clamp_steps(max_steps or settings.max_agent_steps),
            ),
            name=f"execution:{execution.id}",
        )
        self._tasks[execution.id] = task
        task.add_done_callback(lambda _t, eid=execution.id: self._tasks.pop(eid, None))
        return execution

    async def wait(self, execution_id: str) -> ExecutionOutcome | None:
        task = self._tasks.get(execution_id)
        if task is None:
            return None
        return await task

    def is_running(self, execution_id: str) -> bool:
        task = self._tasks.get(execution_id)
        return task is not None and not task.done()

    async def cancel(self, execution_id: str) -> bool:
        task = self._tasks.get(execution_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def running_executions(self) -> list[str]:
        return [eid for eid, task in self._tasks.items() if not task.done()]

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    async def _run(self, *, execution: Execution, user_input: str, max_steps: int) -> ExecutionOutcome:
        execution_id = execution.id
        execution_id_var.set(execution_id)
        started = time.perf_counter()
        settings = self._settings

        conversation_id = execution.conversation_id
        steps: list[AgentStep] = []
        flow: list[AgentFlowNode] = []
        status = "completed"
        final_answer: str | None = None
        error: str | None = None
        notice: str | None = None

        # The lifecycle frame is emitted first so every consumer observes
        # execution_started before any telemetry for this execution.
        self._emit(
            ExecutionEvent(
                type="execution_started",
                execution_id=execution_id,
                status="running",
                data={"conversation_id": conversation_id, "max_steps": max_steps},
            )
        )
        await self._runtime.begin(execution_id, user_input)

        try:
            history = await self._db.list_messages(conversation_id)
            # Drop the just-stored user message; it is presented separately.
            history = [m for m in history if m.id != execution.user_message_id]
            recent, digest = self._context.build_history_view(history)

            local_available = await self._health.local_available(self._local_provider)
            router = Router(
                worker_keys=self._registry.worker_keys(),
                local_available=local_available,
                enable_local_workers=settings.enable_local_workers,
            )
            if not router.local_usable:
                notice = (
                    "Local MiniCPM worker is offline; requests are being handled by DeepSeek only."
                    if settings.enable_local_workers
                    else "Local workers are disabled in settings; DeepSeek is handling everything."
                )
                await self._runtime.notice(execution_id, notice, level="warning")

            state = ExecutionStateManager(
                execution_id,
                user_input,
                max_steps=max_steps,
                available_agents=["main", *self._registry.worker_keys()],
                history_digest=digest,
            )
            state.set_local_available(router.local_usable)

            final_answer, status, error = await self._step_loop(
                execution_id=execution_id,
                conversation_id=conversation_id,
                user_input=user_input,
                history=recent,
                state=state,
                router=router,
                steps=steps,
                flow=flow,
                max_steps=max_steps,
            )
        except asyncio.CancelledError:
            status = "cancelled"
            error = "Execution cancelled."
            log.info("execution_cancelled", extra={"execution_id": execution_id})
            raise
        except Exception as exc:  # noqa: BLE001 - never let a run vanish silently
            status = "failed"
            error = _friendly_error(exc)
            log.exception("execution_failed", extra={"execution_id": execution_id})
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            await self._finalize(
                execution_id=execution_id,
                conversation_id=conversation_id,
                status=status,
                final_answer=final_answer,
                error=error,
                steps=steps,
                notice=notice,
                duration_ms=duration_ms,
            )
            execution_id_var.set(None)

        usage = self._tokens.aggregate(execution_id)
        return ExecutionOutcome(
            execution_id=execution_id,
            conversation_id=conversation_id,
            status=status,
            final_answer=final_answer,
            error=error,
            steps=steps,
            usage=TokenUsage(
                agent_name="execution",
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                total_tokens=usage.total_tokens,
                latency_ms=usage.latency_ms,
                cost_usd=usage.cost_usd,
            ),
            notice=notice,
        )

    async def _step_loop(
        self,
        *,
        execution_id: str,
        conversation_id: str,
        user_input: str,
        history: list[Message],
        state: ExecutionStateManager,
        router: Router,
        steps: list[AgentStep],
        flow: list[AgentFlowNode],
        max_steps: int,
    ) -> tuple[str | None, str, str | None]:
        """Run the plan/route/execute loop. Returns (answer, status, error).

        Step numbering and the budget share one counter, owned by
        :class:`ExecutionStateManager`: every timeline entry (MainAgent turn or
        worker turn) reserves exactly one index, and planning stops at
        ``max_steps``. The finalisation ladder is deliberately allowed to run past
        that budget -- it cannot delegate, so it always terminates, and a run
        that has spent its whole budget is far better off with an answer than
        with a failure.
        """
        final_answer: str | None = None

        while not state.planning_exhausted:
            step_index = state.reserve_index()
            state.set_stage("planning" if step_index == 1 else "reasoning")

            turn = await self._run_main_turn(
                execution_id=execution_id,
                user_input=user_input,
                history=history,
                state=state,
                steps=steps,
                flow=flow,
                step_index=step_index,
                stage=state.current_stage,
            )
            if turn is None:
                return None, "failed", "DeepSeek MainAgent did not return a usable decision."

            decision = turn.decision
            self._emit(
                ExecutionEvent(
                    type="decision",
                    execution_id=execution_id,
                    agent=MAIN_AGENT_KEY,
                    step_index=step_index,
                    data={
                        "action": decision.action,
                        "agent": decision.agent,
                        "task": _clip(decision.task, 400),
                        "reason": _clip(decision.reason, 300),
                        "expected_format": decision.expected_format,
                        "parse_ok": turn.parse.ok,
                        "parse_strategy": turn.parse.strategy,
                    },
                )
            )

            route = router.route(decision)
            if route.notice:
                state.record_error(route.notice)
                state.set_hint(route.notice)
                await self._runtime.notice(execution_id, route.notice, level="warning")

            # ---- final answer -------------------------------------------
            if route.mode == "finalize":
                answer = (decision.answer or "").strip()
                if not answer:
                    # The model chose to answer but produced no text (common
                    # after a truncated JSON repair). Ask once, explicitly.
                    turn = await self._finalize_turn(
                        execution_id=execution_id,
                        user_input=user_input,
                        history=history,
                        state=state,
                        steps=steps,
                        flow=flow,
                        step_index=step_index,
                    )
                    answer = (turn.decision.answer or "").strip() if turn else ""
                if not answer:
                    return None, "failed", "The MainAgent produced an empty final answer."

                final_answer = answer
                state.set_stage("done")
                await self._emit_final_answer(execution_id, answer)
                return final_answer, "completed", None

            # ---- worker dispatch ----------------------------------------
            if route.mode == "worker" and route.agent_key:
                if state.planning_exhausted:
                    # No room for another timeline entry; let the main agent
                    # absorb the work instead of overrunning the budget.
                    state.record_error(
                        f"No step budget left to run {route.agent_key}; handle it yourself."
                    )
                    state.set_hint(
                        "The step budget is nearly exhausted. Produce the final answer now "
                        'with {"action":"answer","answer":"..."}.'
                    )
                    continue

                result, step = await self._run_worker(
                    execution_id=execution_id,
                    agent_key=route.agent_key,
                    decision_task=decision.task or "",
                    decision_context=decision.context or "",
                    state=state,
                    steps=steps,
                    step_index=state.reserve_index(),
                )
                label = self._label_for(route.agent_key)
                state.record_result(result, label=label)
                flow.append(
                    AgentFlowNode(
                        index=step.step_index,
                        agent_name=route.agent_key,
                        agent_label=label,
                        model=step.model,
                        is_local=step.is_local,
                        status=step.status,
                        stage=step.stage,
                    )
                )
                self._emit_flow(execution_id, flow)
                state.set_hint(
                    "Use the worker result above. If it is wrong or incomplete, fix it yourself "
                    "or re-delegate once with a sharper task."
                )
                if step.status == "failed":
                    state.set_hint(
                        "The worker failed. Complete this part yourself and continue; "
                        "do not retry the same worker with the same task."
                    )
                continue

            # ---- main handles it itself ---------------------------------
            state.set_hint(route.notice or "Continue: produce the next step or the final answer.")
            state.note(f"main: {decision.summary_line()}")
            continue

        # Budget exhausted -> force a final answer from gathered material.
        await self._runtime.notice(
            execution_id,
            f"Step budget of {max_steps} reached; asking the MainAgent to finalise.",
            level="warning",
        )
        state.set_stage("finalizing")
        answer = await self._force_final_answer(
            execution_id=execution_id,
            user_input=user_input,
            history=history,
            state=state,
            steps=steps,
            flow=flow,
        )
        if not answer:
            return None, "failed", "Step budget exhausted and no final answer could be produced."
        await self._emit_final_answer(execution_id, answer)
        return answer, "completed", None

    async def _force_final_answer(
        self,
        *,
        execution_id: str,
        user_input: str,
        history: list[Message],
        state: ExecutionStateManager,
        steps: list[AgentStep],
        flow: list[AgentFlowNode],
    ) -> str:
        """Ask the MainAgent for an answer, escalating explicitness on refusal.

        A model that has spent the whole run delegating sometimes keeps emitting
        ``delegate`` even when told to stop. Rather than failing the task, the
        instruction gets more direct, and the final rung abandons the JSON
        protocol entirely and asks for plain Markdown, which is still a perfectly
        good answer.
        """
        for instruction in _FINALIZE_LADDER[:-1]:
            state.set_hint(instruction)
            turn = await self._run_main_turn(
                execution_id=execution_id,
                user_input=user_input,
                history=history,
                state=state,
                steps=steps,
                flow=flow,
                step_index=state.reserve_index(),
                stage="finalizing",
                extra_instruction=instruction,
            )
            if turn is None:
                continue

            answer = (turn.decision.answer or "").strip()
            if answer:
                return answer

            # The model may have abandoned the JSON protocol and written prose.
            raw = (turn.raw or "").strip()
            if raw and not raw.startswith("{"):
                return raw

            state.record_error("MainAgent refused to finalise; asking again more directly.")

        return await self._plain_text_final_answer(
            execution_id=execution_id,
            user_input=user_input,
            history=history,
            state=state,
            steps=steps,
            flow=flow,
        )

    async def _plain_text_final_answer(
        self,
        *,
        execution_id: str,
        user_input: str,
        history: list[Message],
        state: ExecutionStateManager,
        steps: list[AgentStep],
        flow: list[AgentFlowNode],
    ) -> str:
        """Last resort: ask for plain text and stream it, with no JSON at all."""
        main = self._registry.main
        step_index = state.reserve_index()
        stage = "finalizing"
        state.set_stage(stage)
        state.set_hint(_FINALIZE_LADDER[-1])

        await self._runtime.set_active_agent(
            execution_id,
            agent_key=MAIN_AGENT_KEY,
            agent_label=main.label,
            model=main.model,
            is_local=False,
            stage=stage,
            status="thinking",
        )
        self._emit(
            ExecutionEvent(
                type="agent_started",
                execution_id=execution_id,
                agent=MAIN_AGENT_KEY,
                agent_label=main.label,
                model=main.model,
                provider=main.provider.name,
                is_local=False,
                stage=stage,
                step_index=step_index,
            )
        )

        step = AgentStep(
            execution_id=execution_id,
            step_index=step_index,
            agent_name=MAIN_AGENT_KEY,
            agent_label=main.label,
            model=main.model,
            is_local=False,
            task=_FINALIZE_LADDER[-1],
            stage=stage,
            status="running",
            started_at=utcnow(),
        )
        step.id = await self._db.add_step(step)
        steps.append(step)
        flow.append(
            AgentFlowNode(
                index=step_index,
                agent_name=MAIN_AGENT_KEY,
                agent_label=main.label,
                model=main.model,
                is_local=False,
                status="running",
                stage=stage,
            )
        )
        self._emit_flow(execution_id, flow)

        collected: list[str] = []
        reasoning: list[str] = []
        error: str | None = None
        try:
            async for content_delta, reasoning_delta in main.stream_answer(
                user_goal=user_input,
                agent_state=state.visible_state(),
                history=history,
                extra_instruction=_FINALIZE_LADDER[-1],
            ):
                if reasoning_delta:
                    reasoning.append(reasoning_delta)
                if content_delta:
                    collected.append(content_delta)
                    self._emit(
                        ExecutionEvent(
                            type="final_answer_delta",
                            execution_id=execution_id,
                            delta=content_delta,
                        )
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            error = _friendly_error(exc)
            log.warning("plain_text_finalize_failed", exc_info=True)

        answer = "".join(collected).strip()

        if reasoning:
            self._emit(
                ExecutionEvent(
                    type="agent_reasoning",
                    execution_id=execution_id,
                    agent=MAIN_AGENT_KEY,
                    step_index=step_index,
                    text=_clip("".join(reasoning), 8000),
                )
            )

        step.status = "completed" if answer else "failed"
        step.output = answer or error
        step.error = None if answer else error
        step.reasoning = "".join(reasoning) or None
        step.finished_at = utcnow()
        await self._db.update_step(
            step.id,
            status=step.status,
            output=step.output,
            reasoning=step.reasoning,
            error=step.error,
            finished_at=step.finished_at,
        )

        stream_usage = getattr(main.provider, "last_stream_usage", None)
        usage = getattr(stream_usage, "usage", None)
        if usage is not None:
            await self._record_usage(
                execution_id=execution_id, agent_name=MAIN_AGENT_KEY, usage=usage, step_id=step.id
            )

        self._emit(
            ExecutionEvent(
                type="agent_completed" if answer else "agent_failed",
                execution_id=execution_id,
                agent=MAIN_AGENT_KEY,
                step_index=step_index,
                status=step.status,
                text=_clip(answer, 4000),
                message=None if answer else (error or "no answer produced"),
                level="info" if answer else "error",
            )
        )
        return answer

    # ------------------------------------------------------------------
    # Agent invocations
    # ------------------------------------------------------------------
    async def _run_main_turn(
        self,
        *,
        execution_id: str,
        user_input: str,
        history: list[Message],
        state: ExecutionStateManager,
        steps: list[AgentStep],
        flow: list[AgentFlowNode],
        step_index: int,
        stage: str,
        extra_instruction: str | None = None,
    ) -> MainAgentTurn | None:
        main = self._registry.main
        label = main.label

        await self._runtime.set_active_agent(
            execution_id,
            agent_key=MAIN_AGENT_KEY,
            agent_label=label,
            model=main.model,
            is_local=False,
            stage=stage,
            status="thinking",
        )
        self._emit(
            ExecutionEvent(
                type="agent_started",
                execution_id=execution_id,
                agent=MAIN_AGENT_KEY,
                agent_label=label,
                model=main.model,
                provider=main.provider.name,
                is_local=False,
                stage=stage,
                step_index=step_index,
            )
        )

        step = AgentStep(
            execution_id=execution_id,
            step_index=step_index,
            agent_name=MAIN_AGENT_KEY,
            agent_label=label,
            model=main.model,
            is_local=False,
            task=_main_step_task(state, extra_instruction),
            stage=stage,
            status="running",
            started_at=utcnow(),
        )
        step_id = await self._db.add_step(step)
        step.id = step_id
        steps.append(step)
        flow.append(
            AgentFlowNode(
                index=step_index,
                agent_name=MAIN_AGENT_KEY,
                agent_label=label,
                model=main.model,
                is_local=False,
                status="running",
                stage=stage,
            )
        )
        self._emit_flow(execution_id, flow)

        turn = await main.decide(
            user_goal=user_input,
            agent_state=state.visible_state(),
            history=history,
            extra_instruction=extra_instruction,
        )

        debug_payload = {
            **turn.debug,
            **turn.parse.debug_payload(_clip(turn.raw, 4000)),
        }
        if turn.reasoning:
            self._emit(
                ExecutionEvent(
                    type="agent_reasoning",
                    execution_id=execution_id,
                    agent=MAIN_AGENT_KEY,
                    step_index=step_index,
                    text=_clip(turn.reasoning, 8000),
                )
            )

        if turn.error:
            step.status = "failed"
            step.error = turn.error
            step.finished_at = utcnow()
            await self._db.update_step(
                step_id,
                status="failed",
                error=turn.error,
                finished_at=step.finished_at,
                debug=debug_payload,
            )
            self._emit(
                ExecutionEvent(
                    type="agent_failed",
                    execution_id=execution_id,
                    agent=MAIN_AGENT_KEY,
                    step_index=step_index,
                    message=turn.error,
                    level="error",
                )
            )
            return None

        step.status = "completed"
        step.output = turn.raw
        step.reasoning = turn.reasoning
        step.finished_at = turn.finished_at or utcnow()
        step.usage = turn.usage
        step.debug = debug_payload
        step.input = _clip(turn.prompt, 12000)
        await self._db.update_step(
            step_id,
            status="completed",
            output=step.output,
            reasoning=step.reasoning,
            finished_at=step.finished_at,
            input=step.input,
            debug=debug_payload,
        )

        if turn.usage:
            await self._record_usage(
                execution_id=execution_id,
                agent_name=MAIN_AGENT_KEY,
                usage=turn.usage,
                step_id=step_id,
            )

        self._emit(
            ExecutionEvent(
                type="agent_completed",
                execution_id=execution_id,
                agent=MAIN_AGENT_KEY,
                step_index=step_index,
                status="completed",
                text=_clip(turn.raw, 4000),
            )
        )
        return turn

    async def _finalize_turn(self, **kwargs) -> MainAgentTurn | None:
        return await self._run_main_turn(extra_instruction=_FINALIZE_INSTRUCTION, **kwargs)

    async def _run_worker(
        self,
        *,
        execution_id: str,
        agent_key: str,
        decision_task: str,
        decision_context: str,
        state: ExecutionStateManager,
        steps: list[AgentStep],
        step_index: int,
    ) -> tuple[AgentResult, AgentStep]:
        worker = self._registry.worker(agent_key)
        label = self._label_for(agent_key)

        if worker is None:
            result = AgentResult(
                task_id=f"{execution_id}:{step_index}",
                agent=agent_key,
                status="failed",
                error=f"Agent '{agent_key}' is not registered.",
            )
            step = AgentStep(
                execution_id=execution_id,
                step_index=step_index,
                agent_name=agent_key,
                agent_label=label,
                task=decision_task,
                status="failed",
                error=result.error,
                started_at=utcnow(),
                finished_at=utcnow(),
            )
            step.id = await self._db.add_step(step)
            steps.append(step)
            return result, step

        context = self._context.worker_payload(decision_context or _fallback_context(state))
        stage = _stage_for(agent_key)

        await self._runtime.set_active_agent(
            execution_id,
            agent_key=agent_key,
            agent_label=label,
            model=worker.model,
            is_local=worker.is_local,
            stage=stage,
            status="running",
        )
        self._emit(
            ExecutionEvent(
                type="agent_started",
                execution_id=execution_id,
                agent=agent_key,
                agent_label=label,
                model=worker.model,
                provider=worker.provider.name,
                is_local=worker.is_local,
                stage=stage,
                step_index=step_index,
            )
        )

        step = AgentStep(
            execution_id=execution_id,
            step_index=step_index,
            agent_name=agent_key,
            agent_label=label,
            model=worker.model,
            is_local=worker.is_local,
            task=decision_task,
            stage=stage,
            status="running",
            started_at=utcnow(),
        )
        step_id = await self._db.add_step(step)
        step.id = step_id
        steps.append(step)

        agent_task = AgentTask(
            task_id=f"{execution_id}:{step_index}",
            agent=agent_key,
            instruction=decision_task,
            context=context,
        )

        payload_chars = {"task": len(decision_task), "context": len(context)}
        try:
            result = await worker.run(agent_task, state.visible_state())
        except AgentError as exc:
            result = AgentResult(
                task_id=agent_task.task_id,
                agent=agent_key,
                status="failed",
                error=str(exc),
                started_at=step.started_at,
                finished_at=utcnow(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("worker_crashed agent=%s", agent_key)
            result = AgentResult(
                task_id=agent_task.task_id,
                agent=agent_key,
                status="failed",
                error=_friendly_error(exc),
                started_at=step.started_at,
                finished_at=utcnow(),
            )

        # A worker that reported "completed" with no content is a failure in
        # practice -- the MainAgent cannot use an empty string.
        if result.status == "completed" and not (result.output or "").strip():
            result.status = "failed"
            result.error = "Worker returned an empty response."

        step.status = result.status  # type: ignore[assignment]
        step.output = result.output
        step.reasoning = result.reasoning
        step.parsed_output = result.parsed
        step.error = result.error
        step.finished_at = result.finished_at or utcnow()
        step.usage = result.usage
        step.input = _clip(
            f"TASK\n{decision_task}\n\nCONTEXT\n{context}",
            8000,
        )
        step.debug = {"payload_chars": payload_chars, "reasoning_effort": worker.reasoning_effort}

        await self._db.update_step(
            step_id,
            status=step.status,
            output=step.output,
            reasoning=step.reasoning,
            parsed_output=step.parsed_output,
            error=step.error,
            finished_at=step.finished_at,
            input=step.input,
            debug=step.debug,
        )

        if result.reasoning:
            self._emit(
                ExecutionEvent(
                    type="agent_reasoning",
                    execution_id=execution_id,
                    agent=agent_key,
                    step_index=step_index,
                    text=_clip(result.reasoning, 8000),
                )
            )

        if result.usage:
            await self._record_usage(
                execution_id=execution_id,
                agent_name=agent_key,
                usage=result.usage,
                step_id=step_id,
            )

        if result.status == "completed":
            self._emit(
                ExecutionEvent(
                    type="agent_completed",
                    execution_id=execution_id,
                    agent=agent_key,
                    agent_label=label,
                    step_index=step_index,
                    status="completed",
                    text=_clip(result.output, 4000),
                )
            )
        else:
            self._emit(
                ExecutionEvent(
                    type="agent_failed",
                    execution_id=execution_id,
                    agent=agent_key,
                    agent_label=label,
                    step_index=step_index,
                    message=result.error or "worker failed",
                    level="error",
                )
            )
        return result, step

    # ------------------------------------------------------------------
    # Telemetry helpers
    # ------------------------------------------------------------------
    async def _record_usage(
        self,
        *,
        execution_id: str,
        agent_name: str,
        usage: TokenUsage,
        step_id: int | None,
    ) -> None:
        await self._tokens.record(
            usage, execution_id=execution_id, agent_name=agent_name, agent_step_id=step_id
        )
        await self._runtime.apply_usage(execution_id, self._tokens.aggregate(execution_id))

    async def _emit_final_answer(self, execution_id: str, answer: str) -> None:
        # Emit in word-ish chunks so the UI renders a streaming cursor, then a
        # terminal frame carrying the full text.
        chunk_size = 24
        for start in range(0, len(answer), chunk_size):
            self._emit(
                ExecutionEvent(
                    type="final_answer_delta",
                    execution_id=execution_id,
                    delta=answer[start : start + chunk_size],
                )
            )
            await asyncio.sleep(0)
        self._emit(
            ExecutionEvent(type="final_answer", execution_id=execution_id, text=answer)
        )

    async def _finalize(
        self,
        *,
        execution_id: str,
        conversation_id: str,
        status: str,
        final_answer: str | None,
        error: str | None,
        steps: list[AgentStep],
        notice: str | None,
        duration_ms: float,
    ) -> None:
        usage = self._tokens.aggregate(execution_id)
        rollup = TokenUsage(
            agent_name="execution",
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            total_tokens=usage.total_tokens,
            latency_ms=usage.latency_ms,
            cost_usd=usage.cost_usd,
        )

        assistant_message_id: int | None = None
        if final_answer:
            assistant_message_id = await self._db.add_message(
                conversation_id,
                "assistant",
                final_answer,
                execution_id=execution_id,
                usage=rollup,
            )

        await self._db.finish_execution(
            execution_id,
            status=status,
            final_answer=final_answer,
            error=error,
            assistant_message_id=assistant_message_id,
            step_count=len(steps),
            usage=rollup,
        )

        # name the conversation after its first user turn
        messages = await self._db.list_messages(conversation_id, limit=3)
        conversation = await self._db.get_conversation(conversation_id)
        if conversation and conversation.title in ("New conversation", "") and messages:
            await self._db.rename_conversation(conversation_id, _derive_title(messages[0].content))

        await self._runtime.finish(execution_id, status=status, stage=_final_stage(status), error=error)

        terminal_type = {
            "completed": "execution_completed",
            "cancelled": "execution_cancelled",
        }.get(status, "execution_failed")
        self._emit(
            ExecutionEvent(
                type=terminal_type,  # type: ignore[arg-type]
                execution_id=execution_id,
                status=status,
                message=error,
                level="error" if status == "failed" else "info",
                usage=rollup,
                data={
                    "duration_ms": duration_ms,
                    "step_count": len(steps),
                    "totals": usage.model_dump(mode="json"),
                    "by_agent": {k: v.model_dump(mode="json") for k, v in self._tokens.by_agent(execution_id).items()},
                    "notice": notice,
                },
            )
        )
        log.info(
            "execution_finished",
            extra={
                "execution_id": execution_id,
                "status": status,
                "duration": round(duration_ms, 1),
                "steps": len(steps),
                "total_tokens": usage.total_tokens,
            },
        )

    def _emit_flow(self, execution_id: str, flow: list[AgentFlowNode]) -> None:
        self._emit(
            ExecutionEvent(
                type="flow_update",
                execution_id=execution_id,
                data={"flow": [node.model_dump(mode="json") for node in flow]},
            )
        )

    def _emit(self, event: ExecutionEvent) -> None:
        self._bus.publish(event)

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------
    def _label_for(self, agent_key: str) -> str:
        agent = self._registry.get(agent_key)
        return agent.label if agent else agent_key


# --------------------------------------------------------------------------
# Module-level helpers
# --------------------------------------------------------------------------

_FINALIZE_INSTRUCTION = (
    "You must finish now. Do not delegate again. Using everything gathered above, produce the "
    'final answer for the user by replying with a single JSON object: '
    '{"action":"answer","answer":"<your answer in Markdown>"}. '
    "If part of the request could not be completed, state that plainly inside the answer."
)

#: Escalating instructions used when the step budget runs out. The first rungs
#: keep the JSON protocol; the last one abandons it and asks for plain Markdown,
#: which the orchestrator streams and accepts as the answer.
_FINALIZE_LADDER: tuple[str, ...] = (
    _FINALIZE_INSTRUCTION,
    (
        "STOP. You have used your entire step budget and delegating is no longer possible. "
        'Reply with ONLY this JSON object, nothing else: {"action":"answer","answer":"..."}'
    ),
    (
        "Answer the user directly in plain Markdown text. Do not use JSON. "
        "Do not delegate. Write the complete final answer now."
    ),
)


def _clamp_steps(value: int) -> int:
    return max(1, min(int(value or DEFAULT_MAX_STEPS), 64))


def _stage_for(agent_key: str) -> str:
    return {
        "local_extractor": "extracting",
        "local_summarizer": "summarizing",
        "local_classifier": "classifying",
        "local_reviewer": "reviewing",
    }.get(agent_key, "working")


def _final_stage(status: str) -> str:
    return {"completed": "done", "cancelled": "cancelled"}.get(status, "failed")


def _fallback_context(state: ExecutionStateManager) -> str:
    """When the MainAgent supplies no context, hand the worker what it must have.

    Uses the most recent recorded result, never the conversation.
    """
    latest = state.latest_result()
    return latest[1] if latest else ""


def _main_step_task(state: ExecutionStateManager, extra_instruction: str | None) -> str:
    if extra_instruction:
        return extra_instruction
    return f"Timeline step {state.step_index}: decide the next action for the user's request."


def _clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[:limit] + "\n...[truncated]"


def _friendly_error(exc: Exception) -> str:
    """Map exceptions onto text safe to show a user."""
    if isinstance(exc, ProviderUnavailable):
        return str(exc)
    if isinstance(exc, ProviderError):
        return str(exc)
    if isinstance(exc, asyncio.TimeoutError):
        return "A model call timed out. Please try again."
    return f"{type(exc).__name__}: {exc}"


def _derive_title(text: str, limit: int = 60) -> str:
    clean = " ".join((text or "").split())
    if not clean:
        return "New conversation"
    return clean[:limit] + ("..." if len(clean) > limit else "")


__all__ = ["ExecutionOutcome", "Orchestrator"]
