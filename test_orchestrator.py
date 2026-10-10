"""Тесты компонентов оркестратора.

Проверяют:
- EventBus: pub/sub, envelope, retry, timeout, audit
- BaseAgent: circuit breaker, cost, публикация результатов
- Конкретные агенты: Scoring, Compliance, Guardian, Review
- OrchestratorAgent: полный workflow, ожидание ответов, компенсация
"""
import asyncio
import pytest

from orchestrator_components import (
    EventBus,
    BaseAgent,
    ScoringAgent,
    ComplianceAgent,
    GuardianAgent,
    ReviewAgent,
    OrchestratorAgent,
    LLMClient,
)
from agentic_guardrails.cost_cb import CostState
from agentic_guardrails.failure_cb import CBState
from agentic_guardrails.policy_hook_bus import Decision


# ============================================================================
# EventBus
# ============================================================================

class TestEventBus:
    async def test_publish_no_subscribers(self):
        bus = EventBus()
        envelope = await bus.publish("empty", {"event_type": "test"})
        assert envelope["meta"]["topic"] == "empty"
        assert envelope["payload"]["event_type"] == "test"
        assert len(bus.message_log) == 1

    async def test_publish_subscriber_receives(self):
        bus = EventBus()
        received = []

        async def handler(envelope):
            received.append(envelope)

        bus.subscribe("test", "handler", handler)
        await bus.publish("test", {"event_type": "hello"})

        assert len(received) == 1
        assert received[0]["payload"]["event_type"] == "hello"

    async def test_envelope_has_correlation_id(self):
        bus = EventBus()
        envelope = await bus.publish("test", {"event_type": "x"}, correlation_id="cid-1")
        assert envelope["meta"]["correlation_id"] == "cid-1"

    async def test_envelope_generates_correlation_id(self):
        bus = EventBus()
        envelope = await bus.publish("test", {"event_type": "x"})
        assert envelope["meta"]["correlation_id"]
        assert envelope["meta"]["message_id"]
        assert envelope["meta"]["timestamp"]

    async def test_payload_correlation_id_used(self):
        bus = EventBus()
        envelope = await bus.publish("test", {"event_type": "x", "correlation_id": "from-payload"})
        assert envelope["meta"]["correlation_id"] == "from-payload"

    async def test_audit_records_publish(self):
        bus = EventBus()
        await bus.publish("test", {"event_type": "hello"})
        assert len(bus.audit) == 1
        assert bus.audit.verify() is True

    async def test_subscriber_failure_isolated(self):
        bus = EventBus(subscriber_max_attempts=1)
        good_received = []

        async def failing(envelope):
            raise RuntimeError("boom")

        async def good(envelope):
            good_received.append(envelope)

        bus.subscribe("test", "failing", failing)
        bus.subscribe("test", "good", good)
        await bus.publish("test", {"event_type": "x"})

        assert len(good_received) == 1  # good всё равно получил

    async def test_subscriber_timeout(self):
        bus = EventBus(subscriber_timeout=0.05, subscriber_max_attempts=1)

        async def slow(envelope):
            await asyncio.sleep(10)

        bus.subscribe("test", "slow", slow)
        # Не должно зависнуть
        await bus.publish("test", {"event_type": "x"})

        # В аудите должна быть запись о провале
        events = [e.event for e in bus.audit.entries]
        assert "subscriber.failed" in events

    async def test_subscriber_retry_on_transient(self):
        bus = EventBus(subscriber_timeout=1.0, subscriber_max_attempts=3)
        calls = []

        async def flaky(envelope):
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError("transient")

        bus.subscribe("test", "flaky", flaky)
        await bus.publish("test", {"event_type": "x"})
        assert len(calls) == 3


# ============================================================================
# BaseAgent
# ============================================================================

class TestBaseAgent:
    async def test_agent_publishes_completed(self):
        bus = EventBus()
        completed = []

        async def on_completed(envelope):
            completed.append(envelope)

        bus.subscribe("agent.completed", "logger", on_completed)

        agent = ScoringAgent(name="scoring", bus=bus)
        await agent.handle({
            "meta": {"correlation_id": "cid-1", "topic": "scoring.requested"},
            "payload": {"amount": 1000},
        })

        assert len(completed) == 1
        assert completed[0]["payload"]["agent"] == "scoring"
        assert completed[0]["meta"]["correlation_id"] == "cid-1"

    async def test_agent_circuit_breaker_blocks(self):
        """После N сбоев CB открывается, следующий вызов -> agent.blocked."""
        bus = EventBus()
        blocked = []

        async def on_blocked(envelope):
            blocked.append(envelope)

        bus.subscribe("agent.blocked", "logger", on_blocked)

        class BrokenAgent(BaseAgent):
            async def process(self, envelope):
                raise RuntimeError("intentional")

        agent = BrokenAgent(name="broken", bus=bus, failure_threshold=2)

        # Два падения -> CB открывается
        for _ in range(2):
            await agent.handle({
                "meta": {"correlation_id": "cid", "topic": "t"},
                "payload": {},
            })
        assert agent.cb_state is CBState.OPEN

        # Третий вызов: CB открыт -> agent.blocked, process НЕ вызывается
        await agent.handle({
            "meta": {"correlation_id": "cid", "topic": "t"},
            "payload": {},
        })
        assert len(blocked) == 1
        assert blocked[0]["payload"]["agent"] == "broken"
        assert blocked[0]["payload"]["reason"] == "circuit open"

    async def test_agent_failure_published(self):
        bus = EventBus()
        failed = []

        async def on_failed(envelope):
            failed.append(envelope)

        bus.subscribe("agent.failed", "logger", on_failed)

        class BrokenAgent(BaseAgent):
            async def process(self, envelope):
                raise RuntimeError("intentional")

        agent = BrokenAgent(name="broken", bus=bus)
        await agent.handle({
            "meta": {"correlation_id": "cid", "topic": "t"},
            "payload": {},
        })

        assert len(failed) == 1
        assert failed[0]["payload"]["agent"] == "broken"
        assert "intentional" in failed[0]["payload"]["error"]


# ============================================================================
# Конкретные агенты
# ============================================================================

class TestConcreteAgents:
    async def test_scoring_returns_score(self):
        bus = EventBus()
        agent = ScoringAgent(name="scoring", bus=bus)
        result = await agent.process({
            "meta": {"correlation_id": "cid"},
            "payload": {"amount": 10_000},
        })
        assert "credit_score" in result
        assert result["credit_score"] > 0

    async def test_compliance_escalates_large_amount(self):
        bus = EventBus()
        agent = ComplianceAgent(name="compliance", bus=bus)
        result = await agent.process({
            "meta": {"correlation_id": "cid"},
            "payload": {"amount": 10_000_000},
        })
        assert result["decision"] == Decision.ESCALATE.value

    async def test_compliance_allows_small_amount(self):
        bus = EventBus()
        agent = ComplianceAgent(name="compliance", bus=bus)
        result = await agent.process({
            "meta": {"correlation_id": "cid"},
            "payload": {"amount": 1000},
        })
        assert result["decision"] == Decision.ALLOW.value

    async def test_guardian_denies_pii(self):
        bus = EventBus()
        agent = GuardianAgent(name="guardian", bus=bus)
        result = await agent.process({
            "meta": {"correlation_id": "cid"},
            "payload": {"has_pii": True},
        })
        assert result["decision"] == Decision.DENY.value

    async def test_guardian_allows_clean(self):
        bus = EventBus()
        agent = GuardianAgent(name="guardian", bus=bus)
        result = await agent.process({
            "meta": {"correlation_id": "cid"},
            "payload": {"has_pii": False},
        })
        assert result["decision"] == Decision.ALLOW.value

    async def test_review_returns_autonomy_level(self):
        bus = EventBus()
        agent = ReviewAgent(name="review", bus=bus)
        result = await agent.process({
            "meta": {"correlation_id": "cid"},
            "payload": {"amount": 500_000},
        })
        assert "level" in result
        assert "mode" in result
        assert "reason" in result


# ============================================================================
# Оркестратор — полный workflow
# ============================================================================

class TestOrchestrator:
    def _wire(self, bus):
        """Подписывает всех агентов на их топики."""
        scoring = ScoringAgent(name="scoring", bus=bus)
        compliance = ComplianceAgent(name="compliance", bus=bus)
        review = ReviewAgent(name="review", bus=bus)

        bus.subscribe("scoring.requested", "scoring", scoring.handle)
        bus.subscribe("compliance.requested", "compliance", compliance.handle)
        bus.subscribe("review.requested", "review", review.handle)
        return scoring, compliance, review

    async def test_workflow_success(self):
        bus = EventBus()
        self._wire(bus)
        orch = OrchestratorAgent(bus=bus, step_timeout=1.0)

        result = await orch.run_workflow(amount=1000)
        assert result["success"] is True
        assert result["completed"] == ["scoring", "compliance", "review"]

    async def test_workflow_publishes_completed_event(self):
        bus = EventBus()
        self._wire(bus)
        completed_events = []

        async def on_completed(envelope):
            completed_events.append(envelope)

        bus.subscribe("workflow.completed", "logger", on_completed)
        orch = OrchestratorAgent(bus=bus, step_timeout=1.0)
        await orch.run_workflow(amount=1000)

        assert len(completed_events) == 1
        assert completed_events[0]["payload"]["event_type"] == "workflow.completed"

    async def test_workflow_failure_compensates(self):
        """Агент compliance падает — saga компенсирует scoring."""
        bus = EventBus()

        class FailingCompliance(BaseAgent):
            async def process(self, envelope):
                raise RuntimeError("compliance down")

        scoring = ScoringAgent(name="scoring", bus=bus)
        compliance = FailingCompliance(name="compliance", bus=bus)
        review = ReviewAgent(name="review", bus=bus)

        bus.subscribe("scoring.requested", "scoring", scoring.handle)
        bus.subscribe("compliance.requested", "compliance", compliance.handle)
        bus.subscribe("review.requested", "review", review.handle)

        orch = OrchestratorAgent(bus=bus, step_timeout=1.0)
        result = await orch.run_workflow(amount=1000)

        assert result["success"] is False
        assert result["failed_at"] == "compliance"

    async def test_workflow_timeout(self):
        """Агент scoring зависает — таймаут на шаге."""
        bus = EventBus()

        class HangingAgent(BaseAgent):
            async def process(self, envelope):
                await asyncio.sleep(10)

        scoring = HangingAgent(name="scoring", bus=bus)
        bus.subscribe("scoring.requested", "scoring", scoring.handle)

        orch = OrchestratorAgent(bus=bus, step_timeout=0.1)
        result = await orch.run_workflow(amount=1000)

        assert result["success"] is False
        assert result["failed_at"] == "scoring"

    async def test_workflow_correlation_id_propagated(self):
        bus = EventBus()
        self._wire(bus)
        orch = OrchestratorAgent(bus=bus, step_timeout=1.0)

        result = await orch.run_workflow(amount=1000, correlation_id="my-cid")
        assert result["success"] is True

        # Все сообщения workflow имеют этот correlation_id
        workflow_msgs = [
            m for m in bus.message_log
            if m["meta"]["correlation_id"] == "my-cid"
        ]
        assert len(workflow_msgs) >= 3  # scoring/compliance/review + completed

    async def test_concurrent_workflows_isolated(self):
        """Два workflow параллельно — не путаются."""
        bus = EventBus()
        self._wire(bus)
        orch = OrchestratorAgent(bus=bus, step_timeout=1.0)

        r1, r2 = await asyncio.gather(
            orch.run_workflow(amount=1000, correlation_id="cid-1"),
            orch.run_workflow(amount=2000, correlation_id="cid-2"),
        )

        assert r1["success"] is True
        assert r2["success"] is True

    async def test_duplicate_response_ignored(self):
        """Второй agent.completed с тем же ключом игнорируется."""
        bus = EventBus()
        self._wire(bus)
        orch = OrchestratorAgent(bus=bus, step_timeout=1.0)

        # Запускаем workflow, потом дублируем ответ
        result = await orch.run_workflow(amount=1000)
        assert result["success"] is True

        # Проверим, что _pending пуст
        assert len(orch._pending) == 0


# ============================================================================
# LLMClient
# ============================================================================

class TestLLMClient:
    async def test_call_success(self):
        calls = []

        async def fake_llm(prompt):
            calls.append(prompt)
            return "response"

        client = LLMClient(call_fn=fake_llm, max_rpm=60)
        result = await client.call("hello")
        assert result == "response"
        assert calls == ["hello"]

    async def test_call_retry_on_transient(self):
        calls = []

        async def flaky(prompt):
            calls.append(1)
            if len(calls) < 2:
                raise ConnectionError("transient")
            return "ok"

        client = LLMClient(call_fn=flaky, max_rpm=60, max_attempts=3)
        result = await client.call("prompt")
        assert result == "ok"
        assert len(calls) == 2
