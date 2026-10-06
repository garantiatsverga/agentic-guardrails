"""
Монолитный тестовый набор для agentic_guardrails.

Пирамида:
  L1. Unit       — каждый модуль в изоляции (mock-free где возможно)
  L2. Integration— модули вместе (policy + cost + audit)
  L3. E2E        — полный прогон агента: policy -> cost -> saga -> hitl -> evidence -> audit

Запуск:
    pytest -v test_guardrails.py
    pytest -v test_guardrails.py -m e2e
"""
from __future__ import annotations

import asyncio
import time
import pytest

from policy_hook_bus import HookBus, PolicyResult, Decision
from cost_cb import CostBreaker, Budget, CostState
from failure_cb import FailureBreaker, CBState
from hitl import AutonomyLadder, RLevel, run_with_approval
from evidence import EvidenceBundle
from audit import AuditChain
from saga import SagaStep, run_saga


# ============================================================================
# L1. UNIT TESTS
# ============================================================================

class TestPolicyHookBus:
    async def test_allow_when_no_policies(self):
        bus = HookBus()
        result = await bus.check("any_action", {})
        assert result.decision is Decision.ALLOW

    async def test_deny_stops_chain(self):
        bus = HookBus()
        calls = []

        async def p1(action, ctx):
            calls.append("p1")
            return PolicyResult(Decision.ALLOW)

        async def p2(action, ctx):
            calls.append("p2")
            return PolicyResult(Decision.DENY, reason="blocked", policy_id="p2")

        async def p3(action, ctx):
            calls.append("p3")
            return PolicyResult(Decision.ALLOW)

        bus.register("p1", p1)
        bus.register("p2", p2)
        bus.register("p3", p3)

        result = await bus.check("act", {})
        assert result.decision is Decision.DENY
        assert result.policy_id == "p2"
        assert calls == ["p1", "p2"]  # p3 не вызван

    async def test_escalate_propagates(self):
        bus = HookBus()

        async def escalate(action, ctx):
            return PolicyResult(Decision.ESCALATE, reason="amount too high")

        bus.register("limit", escalate)
        result = await bus.check("approve", {"amount": 10_000_000})
        assert result.decision is Decision.ESCALATE
        assert result.reason == "amount too high"


class TestCostBreaker:
    def test_ok_under_soft_threshold(self):
        cb = CostBreaker(
            per_task=Budget(limit=1000),
            per_session=Budget(limit=10_000),
            per_horizon=Budget(limit=100_000),
        )
        assert cb.charge(100) is CostState.OK

    def test_soft_at_70_percent(self):
        cb = CostBreaker(
            per_task=Budget(limit=1000, soft_ratio=0.7),
            per_session=Budget(limit=10_000),
            per_horizon=Budget(limit=100_000),
        )
        assert cb.charge(700) is CostState.SOFT

    def test_hard_at_100_percent(self):
        cb = CostBreaker(
            per_task=Budget(limit=1000),
            per_session=Budget(limit=10_000),
            per_horizon=Budget(limit=100_000),
        )
        assert cb.charge(1000) is CostState.HARD

    def test_task_reset_keeps_session(self):
        cb = CostBreaker(
            per_task=Budget(limit=1000),
            per_session=Budget(limit=10_000),
            per_horizon=Budget(limit=100_000),
        )
        cb.charge(900)
        cb.reset_task()
        assert cb.charge(100) is CostState.OK  # task сброшен, session ещё ок

    def test_horizon_rolls_over(self):
        cb = CostBreaker(
            per_task=Budget(limit=10**9),
            per_session=Budget(limit=10**9),
            per_horizon=Budget(limit=1000),
            horizon_seconds=0.01,
        )
        cb.charge(900)
        time.sleep(0.02)
        assert cb.charge(200) is CostState.OK  # horizon обнулился


class TestFailureBreaker:
    def test_closed_initially(self):
        cb = FailureBreaker(failure_threshold=3)
        assert cb.state is CBState.CLOSED
        assert cb.allow() is True

    def test_opens_after_threshold(self):
        cb = FailureBreaker(failure_threshold=3, recovery_timeout=10)
        for _ in range(3):
            cb.on_failure()
        assert cb.state is CBState.OPEN
        assert cb.allow() is False

    def test_half_open_after_timeout(self):
        cb = FailureBreaker(failure_threshold=2, recovery_timeout=0.01)
        cb.on_failure(); cb.on_failure()
        assert cb.state is CBState.OPEN
        time.sleep(0.02)
        assert cb.state is CBState.HALF_OPEN
        assert cb.allow() is True  # probe

    def test_success_closes(self):
        cb = FailureBreaker(failure_threshold=2, recovery_timeout=0.01)
        cb.on_failure(); cb.on_failure()
        time.sleep(0.02)
        cb.allow()  # probe
        cb.on_success()
        assert cb.state is CBState.CLOSED

    def test_failure_in_half_open_reopens(self):
        cb = FailureBreaker(failure_threshold=2, recovery_timeout=0.01)
        cb.on_failure(); cb.on_failure()
        assert cb.state is CBState.OPEN
        time.sleep(0.02)
        assert cb.state is CBState.HALF_OPEN
        cb.allow()                # probe
        cb.on_failure()           # probe провалился
        assert cb.state is CBState.OPEN   # ← вот правильное ожидание


class TestAutonomyLadder:
    def test_nominal_without_factors(self):
        ladder = AutonomyLadder(nominal=RLevel.R4)
        assert ladder.effective() is RLevel.R4

    def test_effective_subtracts_factors(self):
        ladder = AutonomyLadder(nominal=RLevel.R5)
        assert ladder.effective(criticality=1, risk=1, complexity=1) is RLevel.R2

    def test_clamps_at_zero(self):
        ladder = AutonomyLadder(nominal=RLevel.R1)
        assert ladder.effective(criticality=5, risk=5, complexity=5) is RLevel.R0

    def test_mode_mapping(self):
        ladder = AutonomyLadder(nominal=RLevel.R5)
        assert ladder.mode(RLevel.R0) == "interactive"
        assert ladder.mode(RLevel.R2) == "on_exception"
        assert ladder.mode(RLevel.R3) == "batch"
        assert ladder.mode(RLevel.R5) == "autonomous"


class TestEvidenceBundle:
    def test_add_decision(self):
        b = EvidenceBundle(task_id="t1", agent_id="a1", outcome="success")
        b.add_decision("approve", "ALLOW", "score ok")
        assert len(b.decisions) == 1
        assert b.decisions[0]["action"] == "approve"

    def test_to_json_roundtrip(self):
        import json
        b = EvidenceBundle(task_id="t1", agent_id="a1", outcome="success")
        b.evals = {"accuracy": 0.95}
        data = json.loads(b.to_json())
        assert data["task_id"] == "t1"
        assert data["evals"]["accuracy"] == 0.95


class TestAuditChain:
    def test_empty_chain_verifies(self):
        assert AuditChain().verify() is True

    def test_append_and_verify(self):
        chain = AuditChain()
        chain.append("evt1", {"a": 1})
        chain.append("evt2", {"b": 2})
        assert chain.verify() is True
        assert len(chain.entries) == 2
        assert chain.entries[1].prev_hash == chain.entries[0].entry_hash

    def test_tampering_detected(self):
        chain = AuditChain()
        chain.append("evt1", {"a": 1})
        chain.append("evt2", {"b": 2})
        # портим payload
        chain.entries[0].payload["a"] = 999
        assert chain.verify() is False


class TestSaga:
    async def test_all_steps_succeed(self):
        async def ok(ctx): return "ok"
        async def undo(ctx, out): pass

        result = await run_saga([
            SagaStep("s1", ok, undo),
            SagaStep("s2", ok, undo),
        ])
        assert result.success is True
        assert result.completed == ["s1", "s2"]

    async def test_compensation_in_reverse_order(self):
        order = []

        async def ok(ctx): return "ok"
        async def undo1(ctx, out): order.append("undo1")
        async def undo2(ctx, out): order.append("undo2")

        async def fail(ctx): raise RuntimeError("boom")

        result = await run_saga([
            SagaStep("s1", ok, undo1),
            SagaStep("s2", ok, undo2),
            SagaStep("s3", fail, lambda c, o: None),
        ])
        assert result.success is False
        assert result.failed_at == "s3"
        assert order == ["undo2", "undo1"]  # обратный порядок

    async def test_compensation_error_swallowed(self):
        async def ok(ctx): return "ok"
        async def bad_undo(ctx, out): raise RuntimeError("undo fail")
        async def fail(ctx): raise RuntimeError("boom")

        result = await run_saga([
            SagaStep("s1", ok, bad_undo),
            SagaStep("s2", fail, lambda c, o: None),
        ])
        assert result.success is False  # не падаем из-за ошибки компенсации


# ============================================================================
# L2. INTEGRATION TESTS
# ============================================================================

@pytest.mark.integration
class TestPolicyCostAuditIntegration:
    """Policy решает -> Cost списывает -> Audit пишет. Всё в связке."""

    async def test_full_pipeline_allow(self):
        bus = HookBus()
        cb = CostBreaker(
            per_task=Budget(limit=10_000),
            per_session=Budget(limit=100_000),
            per_horizon=Budget(limit=1_000_000),
        )
        chain = AuditChain()

        async def allow_all(action, ctx):
            return PolicyResult(Decision.ALLOW)

        bus.register("allow", allow_all)

        decision = await bus.check("llm_call", {"prompt": "hi"})
        assert decision.decision is Decision.ALLOW

        cost_state = cb.charge(500)
        assert cost_state is CostState.OK

        chain.append("llm_call", {"cost": 500, "decision": "allow"})
        assert chain.verify() is True

    async def test_policy_deny_blocks_cost(self):
        """Если policy DENY — не тратим токены."""
        bus = HookBus()
        cb = CostBreaker(
            per_task=Budget(limit=1000),
            per_session=Budget(limit=10_000),
            per_horizon=Budget(limit=100_000),
        )

        async def deny_pii(action, ctx):
            if ctx.get("pii") is True:          # ← проверяем значение, не ключ
                return PolicyResult(Decision.DENY, reason="pii")
            return PolicyResult(Decision.ALLOW)

        bus.register("pii", deny_pii)

        decision = await bus.check("llm_call", {"pii": True})
        assert decision.decision is Decision.DENY

        decision2 = await bus.check("llm_call", {"pii": False})
        assert decision2.decision is Decision.ALLOW
        assert cb.charge(500) is CostState.OK


@pytest.mark.integration
class TestCostHITLIntegration:
    """Cost CB SOFT -> эскалация в HITL."""

    async def test_soft_triggers_escalation(self):
        cb = CostBreaker(
            per_task=Budget(limit=1000, soft_ratio=0.7),
            per_session=Budget(limit=10_000),
            per_horizon=Budget(limit=100_000),
        )
        ladder = AutonomyLadder(nominal=RLevel.R5)
        approvals = []

        async def approve(action):
            approvals.append(action)
            return True

        state = cb.charge(800)  # SOFT
        assert state is CostState.SOFT

        level = ladder.effective(criticality=0, risk=0, complexity=0)
        allowed = await run_with_approval(
            ladder, {"llm_call": "expensive"}, approve,
            ctx={"criticality": 0, "risk": 0, "complexity": 0},
        )
        assert allowed is True  # autonomous R5

        # а теперь с риском -> эскалация
        allowed2 = await run_with_approval(
            ladder, {"llm_call": "expensive"}, approve,
            ctx={"criticality": 2, "risk": 2, "complexity": 1},
        )
        assert allowed2 is True
        assert len(approvals) == 1  # потребовалось одобрение


@pytest.mark.integration
class TestFailureCBWithSaga:
    """Failure CB открыт -> Saga откатывает предыдущие шаги."""

    async def test_cb_open_rolls_back(self):
        cb = FailureBreaker(failure_threshold=2, recovery_timeout=60)
        compensations = []

        async def step_ok(ctx): return "ok"
        async def step_fail(ctx):
            if not cb.allow():
                raise RuntimeError("CB open")
            cb.on_failure()
            raise RuntimeError("external failure")

        async def undo(ctx, out): compensations.append("undone")

        # первые два вызова откроют CB
        result1 = await run_saga([
            SagaStep("s1", step_ok, undo),
            SagaStep("s2", step_fail, lambda c, o: None),
        ])
        assert result1.success is False
        assert "undone" in compensations

        compensations.clear()
        # теперь CB открыт -> s2 сразу падает
        result2 = await run_saga([
            SagaStep("s1", step_ok, undo),
            SagaStep("s2", step_fail, lambda c, o: None),
        ])
        assert result2.success is False
        assert cb.state is CBState.OPEN


@pytest.mark.integration
class TestEvidenceWithAudit:
    """Evidence Bundle -> audit chain."""

    async def test_bundle_logged_to_chain(self):
        chain = AuditChain()
        bundle = EvidenceBundle(task_id="t42", agent_id="scoring", outcome="success")
        bundle.add_decision("approve_credit", "ALLOW", "score=0.9")
        bundle.token_cost = 1500

        chain.append("evidence_complete", {
            "task_id": bundle.task_id,
            "outcome": bundle.outcome,
            "token_cost": bundle.token_cost,
        })
        assert chain.verify() is True
        assert chain.entries[-1].payload["token_cost"] == 1500


# ============================================================================
# L3. E2E TESTS
# ============================================================================

@pytest.mark.e2e
class TestEndToEndCreditFlow:
    """
    Полный прогон «кредитной заявки» через все модули:
      policy -> cost -> saga -> hitl -> evidence -> audit
    Без банковской специфики — просто имитация агентного workflow.
    """

    async def test_happy_path_auto_approved(self):
        # --- setup ---
        bus = HookBus()
        cb = CostBreaker(
            per_task=Budget(limit=10_000),
            per_session=Budget(limit=100_000),
            per_horizon=Budget(limit=1_000_000),
        )
        ladder = AutonomyLadder(nominal=RLevel.R5)
        chain = AuditChain()
        bundle = EvidenceBundle(
            task_id="credit-001", agent_id="orchestrator", outcome="success",
        )

        async def aml_ok(action, ctx):
            return PolicyResult(Decision.ALLOW)

        bus.register("aml", aml_ok)

        # --- workflow шаги ---
        async def extract(ctx):
            chain.append("extract", {"task_id": ctx["task_id"]})
            return {"docs": "extracted"}

        async def score(ctx):
            chain.append("score", {"task_id": ctx["task_id"]})
            return {"score": 0.87}

        async def approve(ctx):
            chain.append("approve", {"task_id": ctx["task_id"]})
            return {"approved": True}

        async def undo_extract(ctx, out): chain.append("undo_extract", {})
        async def undo_score(ctx, out): chain.append("undo_score", {})
        async def undo_approve(ctx, out): chain.append("undo_approve", {})

        # --- policy check перед каждым шагом ---
        for action in ["extract", "score", "approve"]:
            d = await bus.check(action, {"task_id": "credit-001"})
            assert d.decision is Decision.ALLOW

        # --- cost accounting ---
        assert cb.charge(2000) is CostState.OK
        assert cb.charge(2000) is CostState.OK

        # --- saga ---
        result = await run_saga([
            SagaStep("extract", extract, undo_extract),
            SagaStep("score", score, undo_score),
            SagaStep("approve", approve, undo_approve),
        ], context={"task_id": "credit-001"})

        assert result.success is True
        assert result.completed == ["extract", "score", "approve"]

        # --- hitl ---
        async def approve_fn(action): return True
        allowed = await run_with_approval(
            ladder, {"action": "final_release"}, approve_fn,
            ctx={"criticality": 0, "risk": 0, "complexity": 0},
        )
        assert allowed is True

        # --- evidence ---
        for step in result.completed:
            bundle.add_decision(step, "ALLOW", "ok")
        bundle.token_cost = 4000
        chain.append("evidence", {"task_id": bundle.task_id})

        # --- assertions ---
        assert chain.verify() is True
        assert len(bundle.decisions) == 3
        assert bundle.token_cost == 4000
        events = [e.event for e in chain.entries]
        assert events == ["extract", "score", "approve", "evidence"]

    async def test_sad_path_escalation_and_rollback(self):
        """Заявка с высоким риском -> эскалация -> отказ -> откат."""
        bus = HookBus()
        cb = CostBreaker(
            per_task=Budget(limit=10_000),
            per_session=Budget(limit=100_000),
            per_horizon=Budget(limit=1_000_000),
        )
        ladder = AutonomyLadder(nominal=RLevel.R5)
        chain = AuditChain()
        bundle = EvidenceBundle(
            task_id="credit-002", agent_id="orchestrator", outcome="escalated",
        )

        async def aml_flag(action, ctx):
            if ctx.get("amount", 0) > 5_000_000:
                return PolicyResult(Decision.ESCALATE, reason="amount > 5M")
            return PolicyResult(Decision.ALLOW)

        bus.register("aml", aml_flag)

        # policy эскалирует
        d = await bus.check("approve", {"amount": 10_000_000})
        assert d.decision is Decision.ESCALATE

        # cost растёт
        assert cb.charge(9000) is CostState.SOFT

        # saga: два шага ок, третий падает
        compensations = []

        async def s1(ctx): return "ok1"
        async def s2(ctx): return "ok2"
        async def s3(ctx): raise RuntimeError("compliance failed")

        async def u1(ctx, out): compensations.append("undo1")
        async def u2(ctx, out): compensations.append("undo2")
        async def u3(ctx, out): compensations.append("undo3")

        result = await run_saga([
            SagaStep("s1", s1, u1),
            SagaStep("s2", s2, u2),
            SagaStep("s3", s3, u3),
        ])
        assert result.success is False
        assert result.failed_at == "s3"
        assert compensations == ["undo2", "undo1"]

        # HITL: эскалация -> человек отказывает
        approvals = []

        async def deny(action):
            approvals.append(action)
            return False

        allowed = await run_with_approval(
            ladder, {"action": "final_release"}, deny,
            ctx={"criticality": 3, "risk": 3, "complexity": 2},
        )
        assert allowed is False
        assert len(approvals) == 1

        # evidence + audit
        bundle.outcome = "rejected"
        bundle.add_decision("final_release", "DENY", "human rejected")
        chain.append("escalated", {"task_id": bundle.task_id})
        chain.append("rejected", {"task_id": bundle.task_id})

        assert chain.verify() is True
        assert bundle.outcome == "rejected"
        assert [e.event for e in chain.entries] == ["escalated", "rejected"]

    async def test_circuit_breaker_protects_e2e(self):
        """Внешний сервис падает -> CB открывается -> saga откатывает -> audit фиксирует."""
        cb = FailureBreaker(failure_threshold=3, recovery_timeout=60)
        chain = AuditChain()

        async def flaky_call(ctx):
            if not cb.allow():
                chain.append("cb_blocked", {})
                raise RuntimeError("circuit open")
            cb.on_failure()
            raise RuntimeError("service down")

        async def undo(ctx, out):
            chain.append("rollback", {})

        # 3 шага подряд -> CB откроется
        for i in range(4):
            result = await run_saga([
                SagaStep(f"call_{i}", flaky_call, undo),
            ])
            assert result.success is False

        assert cb.state is CBState.OPEN
        assert chain.verify() is True
        assert any(e.event == "cb_blocked" for e in chain.entries)
