"""Компоненты оркестратора для AI-агентной системы.

Набор переиспользуемых компонентов (не полное демо):
- EventBus — pub/sub симуляция Kafka
- OrchestratorAgent — saga + hitl + cost_cb + audit + parallel_runner
- BaseAgent + ScoringAgent, ComplianceAgent, GuardianAgent, ReviewAgent
- LLMClient — обёртка над LLM с rate_limiter + retry_policy

Пример использования — напиши свой main() и собери сценарий.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from agentic_guardrails.audit import AuditChain
from agentic_guardrails.cost_cb import CostBreaker, Budget, CostState
from agentic_guardrails.failure_cb import FailureBreaker, CBState
from agentic_guardrails.hitl import AutonomyLadder, RLevel
from agentic_guardrails.parallel_runner import parallel
from agentic_guardrails.policy_hook_bus import HookBus, PolicyResult, Decision
from agentic_guardrails.rate_limiter import RateLimiter
from agentic_guardrails.retry_policy import RetryPolicy, ExponentialBackoff
from agentic_guardrails.saga import SagaStep, run_saga


# ============================================================================
# EventBus
# ============================================================================

Subscriber = Callable[[dict], Awaitable[None]]


class EventBus:
    """Простейший pub/sub брокер — симуляция Kafka для прототипа."""

    def __init__(
        self,
        audit: AuditChain | None = None,
        subscriber_timeout: float = 5.0,
        subscriber_max_attempts: int = 3,
    ) -> None:
        self._subscribers: dict[str, list[tuple[str, Subscriber]]] = {}
        self._message_log: list[dict] = []
        self._audit = audit or AuditChain()
        self._subscriber_timeout = subscriber_timeout
        self._retry = RetryPolicy(
            max_attempts=subscriber_max_attempts,
            backoff=ExponentialBackoff(base=0.05, factor=2.0, jitter=True),
            retry_on=(TimeoutError, ConnectionError),
        )

    @property
    def audit(self) -> AuditChain:
        return self._audit

    @property
    def message_log(self) -> list[dict]:
        return list(self._message_log)

    def subscribe(self, topic: str, name: str, callback: Subscriber) -> None:
        self._subscribers.setdefault(topic, []).append((name, callback))
        print(f"[Broker] {name} подписался на '{topic}'")

    def _make_envelope(
        self,
        topic: str,
        payload: dict,
        correlation_id: str | None = None,
    ) -> dict:
        return {
            "meta": {
                "message_id": str(uuid.uuid4()),
                "correlation_id": (
                    correlation_id
                    or payload.get("correlation_id")
                    or str(uuid.uuid4())
                ),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "topic": topic,
            },
            "payload": payload,
        }

    async def _call_subscriber(
        self,
        name: str,
        callback: Subscriber,
        envelope: dict,
    ) -> None:
        async def attempt() -> None:
            await asyncio.wait_for(
                callback(envelope),
                timeout=self._subscriber_timeout,
            )

        try:
            await self._retry.execute(attempt)
        except Exception as exc:
            self._audit.append("subscriber.failed", {
                "subscriber": name,
                "topic": envelope["meta"]["topic"],
                "message_id": envelope["meta"]["message_id"],
                "correlation_id": envelope["meta"]["correlation_id"],
                "error": str(exc),
                "error_type": type(exc).__name__,
            })
            print(f"[Broker] {name} failed: {exc}")

    async def publish(
        self,
        topic: str,
        payload: dict,
        correlation_id: str | None = None,
    ) -> dict:
        envelope = self._make_envelope(topic, payload, correlation_id)
        self._message_log.append(envelope)

        self._audit.append("message.published", {
            "topic": topic,
            "message_id": envelope["meta"]["message_id"],
            "correlation_id": envelope["meta"]["correlation_id"],
            "event_type": payload.get("event_type"),
        })

        print(f"[Broker] -> {topic}: {payload.get('event_type', '<no type>')}")

        subs = self._subscribers.get(topic, [])
        if subs:
            await parallel(
                [(self._call_subscriber, name, cb, envelope) for name, cb in subs],
                max_concurrency=10,
                timeout_per_task=self._subscriber_timeout + 1.0,
            )
        return envelope


# ============================================================================
# LLMClient
# ============================================================================

class LLMClient:
    """Обёртка над вызовом LLM с rate_limiter и retry_policy."""

    def __init__(
        self,
        call_fn: Callable[[str], Awaitable[str]],
        max_rpm: int = 60,
        max_attempts: int = 3,
    ) -> None:
        self._call_fn = call_fn
        self._limiter = RateLimiter(
            max_tokens=max_rpm,
            refill_rate=max_rpm / 60.0,
        )
        self._retry = RetryPolicy(
            max_attempts=max_attempts,
            backoff=ExponentialBackoff(base=0.1, factor=2.0, jitter=True),
            retry_on=(TimeoutError, ConnectionError),
        )

    async def call(self, prompt: str) -> str:
        await self._limiter.acquire()
        return await self._retry.execute(self._call_fn, prompt)


# ============================================================================
# BaseAgent
# ============================================================================

@dataclass
class BaseAgent:
    """Базовый агент с failure_cb и cost_cb."""

    name: str
    bus: EventBus
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    cost_per_task: int = 1000

    _cb: FailureBreaker = field(init=False)
    _cost: CostBreaker = field(init=False)

    def __post_init__(self) -> None:
        self._cb = FailureBreaker(
            failure_threshold=self.failure_threshold,
            recovery_timeout=self.recovery_timeout,
        )
        self._cost = CostBreaker(
            per_task=Budget(limit=10_000),
            per_session=Budget(limit=100_000),
            per_horizon=Budget(limit=1_000_000),
        )

    async def process(self, envelope: dict) -> dict:
        raise NotImplementedError

    async def handle(self, envelope: dict) -> None:
        correlation_id = envelope["meta"]["correlation_id"]

        if not self._cb.allow():
            await self.bus.publish(
                "agent.blocked",
                {
                    "event_type": "agent.blocked",
                    "agent": self.name,
                    "reason": "circuit open",
                },
                correlation_id=correlation_id,
            )
            return

        cost_state = self._cost.charge(self.cost_per_task)
        if cost_state is CostState.HARD:
            await self.bus.publish(
                "escalation",
                {
                    "event_type": "escalation",
                    "agent": self.name,
                    "reason": "cost budget exhausted",
                },
                correlation_id=correlation_id,
            )
            return

        try:
            response = await self.process(envelope)
            self._cb.on_success()
        except Exception as exc:
            self._cb.on_failure()
            await self.bus.publish(
                "agent.failed",
                {
                    "event_type": "agent.failed",
                    "agent": self.name,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
                correlation_id=correlation_id,
            )
            return

        await self.bus.publish(
            "agent.completed",
            {
                "event_type": "agent.completed",
                "agent": self.name,
                "result": response,
            },
            correlation_id=correlation_id,
        )

    @property
    def cb_state(self) -> CBState:
        return self._cb.state


# ============================================================================
# Конкретные агенты
# ============================================================================

@dataclass
class ScoringAgent(BaseAgent):
    async def process(self, envelope: dict) -> dict:
        await asyncio.sleep(0.01)
        amount = envelope["payload"].get("amount", 0)
        score = min(1000, 500 + amount // 1000)
        return {"credit_score": score}


@dataclass
class ComplianceAgent(BaseAgent):
    _policies: HookBus = field(init=False)

    def __post_init__(self) -> None:
        super().__post_init__()
        self._policies = HookBus(timeout_per_policy=0.1)
        self._policies.register("amount_limit", self._check_amount)

    async def _check_amount(self, action: str, ctx: dict) -> PolicyResult:
        amount = ctx.get("amount", 0)
        if amount > 5_000_000:
            return PolicyResult(
                Decision.ESCALATE,
                reason=f"amount {amount} > 5M",
                policy_id="amount_limit",
            )
        return PolicyResult(Decision.ALLOW)

    async def process(self, envelope: dict) -> dict:
        amount = envelope["payload"].get("amount", 0)
        result = await self._policies.check("approve", {"amount": amount})
        return {
            "decision": result.decision.value,
            "reason": result.reason,
            "policy_id": result.policy_id,
        }


@dataclass
class GuardianAgent(BaseAgent):
    _policies: HookBus = field(init=False)

    def __post_init__(self) -> None:
        super().__post_init__()
        self._policies = HookBus(timeout_per_policy=0.1)
        self._policies.register("no_pii", self._check_pii)

    async def _check_pii(self, action: str, ctx: dict) -> PolicyResult:
        if ctx.get("has_pii"):
            return PolicyResult(
                Decision.DENY,
                reason="PII detected in payload",
                policy_id="no_pii",
            )
        return PolicyResult(Decision.ALLOW)

    async def process(self, envelope: dict) -> dict:
        has_pii = envelope["payload"].get("has_pii", False)
        result = await self._policies.check("process", {"has_pii": has_pii})
        return {"decision": result.decision.value, "reason": result.reason}


@dataclass
class ReviewAgent(BaseAgent):
    _ladder: AutonomyLadder = field(init=False)

    def __post_init__(self) -> None:
        super().__post_init__()
        self._ladder = AutonomyLadder(
            nominal=RLevel.R5,
            approval_timeout=30.0,
        )

    async def process(self, envelope: dict) -> dict:
        amount = envelope["payload"].get("amount", 0)
        decision = self._ladder.decide(
            criticality=int(amount > 1_000_000),
            risk=int(amount > 5_000_000),
            complexity=0,
        )
        return {
            "level": decision.level.name,
            "mode": self._ladder.mode(decision.level),
            "reason": decision.reason,
        }


# ============================================================================
# OrchestratorAgent
# ============================================================================

@dataclass
class OrchestratorAgent:
    """Оркестратор workflow: saga шагов, hitl, cost_cb, audit.

    Публикует команды агентам и ЖДЁТ ответа по (correlation_id, agent)
    с таймаутом step_timeout. Делает шаги saga последовательными.

    Подписки:
    - agent.completed -> resolve future
    - agent.failed    -> reject future
    - agent.blocked   -> reject future
    """
    bus: EventBus
    hitl_ladder: AutonomyLadder = field(
        default_factory=lambda: AutonomyLadder(
            nominal=RLevel.R5,
            approval_timeout=30.0,
        )
    )
    budget: CostBreaker = field(
        default_factory=lambda: CostBreaker(
            per_task=Budget(50_000),
            per_session=Budget(500_000),
            per_horizon=Budget(5_000_000),
        )
    )
    step_timeout: float = 30.0

    _pending: dict = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.bus.subscribe(
            "agent.completed",
            f"orchestrator-{id(self)}-completed",
            self._on_agent_completed,
        )
        self.bus.subscribe(
            "agent.failed",
            f"orchestrator-{id(self)}-failed",
            self._on_agent_failed,
        )
        self.bus.subscribe(
            "agent.blocked",
            f"orchestrator-{id(self)}-blocked",
            self._on_agent_blocked,
        )

    async def _resolve(self, envelope: dict, *, error: Exception | None = None) -> None:
        payload = envelope["payload"]
        key = (envelope["meta"]["correlation_id"], payload.get("agent", ""))
        fut = self._pending.pop(key, None)
        if fut is None or fut.done():
            return
        if error is not None:
            fut.set_exception(error)
        else:
            fut.set_result(payload.get("result", {}))

    async def _on_agent_completed(self, envelope: dict) -> None:
        await self._resolve(envelope)

    async def _on_agent_failed(self, envelope: dict) -> None:
        payload = envelope["payload"]
        err = RuntimeError(
            f"agent {payload.get('agent')} failed: "
            f"{payload.get('error_type')}: {payload.get('error')}"
        )
        await self._resolve(envelope, error=err)

    async def _on_agent_blocked(self, envelope: dict) -> None:
        payload = envelope["payload"]
        err = RuntimeError(
            f"agent {payload.get('agent')} blocked: {payload.get('reason')}"
        )
        await self._resolve(envelope, error=err)

    async def _request(
        self,
        agent: str,
        topic: str,
        payload: dict,
        correlation_id: str,
    ) -> dict:
        key = (correlation_id, agent)
        if key in self._pending:
            raise RuntimeError(f"duplicate request for {key}")

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[key] = fut

        try:
            await self.bus.publish(
                topic,
                {"event_type": f"{agent}.requested", **payload},
                correlation_id=correlation_id,
            )
            return await asyncio.wait_for(fut, timeout=self.step_timeout)
        finally:
            self._pending.pop(key, None)

    async def _step_scoring(self, ctx: dict) -> dict:
        return await self._request(
            agent="scoring",
            topic="scoring.requested",
            payload={"amount": ctx["amount"]},
            correlation_id=ctx["correlation_id"],
        )

    async def _step_compliance(self, ctx: dict) -> dict:
        return await self._request(
            agent="compliance",
            topic="compliance.requested",
            payload={"amount": ctx["amount"]},
            correlation_id=ctx["correlation_id"],
        )

    async def _step_review(self, ctx: dict) -> dict:
        return await self._request(
            agent="review",
            topic="review.requested",
            payload={"amount": ctx["amount"]},
            correlation_id=ctx["correlation_id"],
        )

    async def _undo(self, ctx: dict, out: Any) -> None:
        self.bus.audit.append("step.compensated", {
            "correlation_id": ctx["correlation_id"],
        })

    async def run_workflow(
        self,
        amount: float,
        correlation_id: str | None = None,
    ) -> dict:
        correlation_id = correlation_id or str(uuid.uuid4())

        state = self.budget.charge(5000)
        if state is CostState.HARD:
            await self.bus.publish(
                "escalation",
                {"event_type": "escalation", "reason": "orchestrator budget exhausted"},
                correlation_id=correlation_id,
            )
            return {"success": False, "reason": "budget exhausted"}

        result = await run_saga(
            [
                SagaStep("scoring", self._step_scoring, self._undo),
                SagaStep("compliance", self._step_compliance, self._undo),
                SagaStep("review", self._step_review, self._undo),
            ],
            context={"amount": amount, "correlation_id": correlation_id},
        )

        if not result.success:
            await self.bus.publish(
                "workflow.failed",
                {
                    "event_type": "workflow.failed",
                    "failed_at": result.failed_at,
                    "inconsistent": result.inconsistent,
                },
                correlation_id=correlation_id,
            )
            return {
                "success": False,
                "failed_at": result.failed_at,
                "inconsistent": result.inconsistent,
                "errors": [str(e) for e in result.compensation_errors],
            }

        await self.bus.publish(
            "workflow.completed",
            {"event_type": "workflow.completed", "amount": amount},
            correlation_id=correlation_id,
        )
        return {"success": True, "completed": result.completed}
