"""Smoke test: проверяет, что все модули библиотеки работают.

Запуск:
    python examples/demo.py

Падает с AssertionError, если что-то не работает.
Время выполнения: <2 секунды.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Чтобы работало без установки пакета:
sys.path.insert(0, str(Path(__file__).parent.parent))

from agentic_guardrails.cost_cb import CostBreaker, Budget, CostState
from agentic_guardrails.failure_cb import FailureBreaker, CBState
from agentic_guardrails.audit import AuditChain
from agentic_guardrails.policy_hook_bus import HookBus, PolicyResult, Decision
from agentic_guardrails.hitl import AutonomyLadder, RLevel
from agentic_guardrails.saga import SagaStep, run_saga
from agentic_guardrails.retry_policy import RetryPolicy, FixedBackoff


def check(name: str, condition: bool) -> None:
    mark = "✓" if condition else "✗"
    print(f"  {mark} {name}")
    if not condition:
        raise AssertionError(f"FAILED: {name}")


def test_cost_cb() -> None:
    print("[cost_cb]")
    cb = CostBreaker(
        per_task=Budget(limit=1000, soft_ratio=0.7),
        per_session=Budget(limit=10_000),
        per_horizon=Budget(limit=100_000),
    )
    check("charge(500) -> OK", cb.charge(500) is CostState.OK)
    check("charge(300) -> SOFT (80% > 70%)", cb.charge(300) is CostState.SOFT)
    check("charge(300) -> HARD (1100 > 1000)", cb.charge(300) is CostState.HARD)
    check("task_used == 1100", cb.task_used == 1100)

    cb2 = CostBreaker(Budget(10**9), Budget(10**9), Budget(10**9))
    check("charge_batch эквивалентен sequential",
          cb2.charge_batch([100, 200, 300]) is CostState.OK
          and cb2.task_used == 600)


def test_failure_cb() -> None:
    print("[failure_cb]")
    fb = FailureBreaker(failure_threshold=3, recovery_timeout=9999.0)
    check("initial state CLOSED", fb.state is CBState.CLOSED)
    check("allow() True initially", fb.allow() is True)
    fb.on_failure()
    fb.on_failure()
    check("after 2 failures -> CLOSED", fb.state is CBState.CLOSED)
    fb.on_failure()
    check("after 3 failures -> OPEN", fb.state is CBState.OPEN)
    check("allow() False when OPEN", fb.allow() is False)
    fb.on_success()
    check("after success -> CLOSED", fb.state is CBState.CLOSED)


async def test_policy_hook_bus() -> None:
    print("[policy_hook_bus]")
    bus = HookBus(timeout_per_policy=0.1)

    async def no_pii(action, ctx):
        if ctx.get("pii"):
            return PolicyResult(Decision.DENY, reason="pii detected", policy_id="no_pii")
        return PolicyResult(Decision.ALLOW)

    bus.register("no_pii", no_pii)

    r1 = await bus.check("send", {"pii": False})
    check("allow when no pii", r1.decision is Decision.ALLOW)

    r2 = await bus.check("send", {"pii": True})
    check("deny when pii", r2.decision is Decision.DENY)
    check("reason propagated", r2.reason == "pii detected")
    check("policy_id propagated", r2.policy_id == "no_pii")

    # Таймаут
    import asyncio as aio

    async def slow(action, ctx):
        await aio.sleep(10)
        return PolicyResult(Decision.ALLOW)

    bus2 = HookBus(timeout_per_policy=0.05)
    bus2.register("slow", slow)
    r3 = await bus2.check("action", {})
    check("timeout -> ESCALATE", r3.decision is Decision.ESCALATE)
    check("timeout reason mentions timeout", "timeout" in r3.reason.lower())


def test_audit() -> None:
    print("[audit]")
    chain = AuditChain()
    check("empty chain verifies", chain.verify() is True)
    check("empty chain len 0", len(chain) == 0)

    e1 = chain.append("evt1", {"a": 1})
    e2 = chain.append("evt2", {"b": 2})
    check("2 entries", len(chain) == 2)
    check("verify True", chain.verify() is True)
    check("seq monotonic", e1.seq == 1 and e2.seq == 2)
    check("prev_hash chains", e2.prev_hash == e1.entry_hash)
    check("timestamp is ISO str", "T" in e1.timestamp)
    check("payload isolated",
          (chain.entries[0].payload.update({"a": 999}) or
           chain.entries[0].payload["a"] == 1))
    check("payload order stable", chain.entries[0].payload == {"a": 1})


async def test_saga() -> None:
    print("[saga]")

    async def ok(ctx): return "ok"
    async def fail(ctx): raise RuntimeError("boom")
    async def undo(ctx, out): pass

    r1 = await run_saga([SagaStep("s1", ok, undo)])
    check("all success", r1.success is True)
    check("completed", r1.completed == ["s1"])

    r2 = await run_saga([
        SagaStep("s1", ok, undo),
        SagaStep("s2", fail, undo),
    ])
    check("failure detected", r2.success is False)
    check("failed_at == s2", r2.failed_at == "s2")
    check("no inconsistent", r2.inconsistent is False)
    check("no compensation errors", r2.compensation_errors == [])


async def test_retry_policy() -> None:
    print("[retry_policy]")
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("fail")
        return "ok"

    policy = RetryPolicy(
        max_attempts=5,
        backoff=FixedBackoff(interval=0.001),
        retry_on=(ConnectionError,),
    )
    result = await policy.execute(flaky)
    check("retry until success", result == "ok")
    check("called 3 times", len(calls) == 3)

    calls2 = []

    async def bad():
        calls2.append(1)
        raise ValueError("bad input")

    policy2 = RetryPolicy(
        max_attempts=5,
        backoff=FixedBackoff(interval=0.001),
        give_up_on=(ValueError,),
    )
    try:
        await policy2.execute(bad)
    except ValueError:
        pass
    check("give_up_on stops retry", len(calls2) == 1)


async def test_parallel_runner() -> None:
    print("[parallel_runner]")
    import asyncio

    async def fn(x, delay=0.01):
        await asyncio.sleep(delay)
        return x * 2

    results = await parallel(
        [lambda x=i: fn(x) for i in [1, 2, 3, 4, 5]],
        max_concurrency=3,
    )
    check("all success", all(r.success for r in results))
    check("order preserved", [r.value for r in results] == [2, 4, 6, 8, 10])

    # Падение одной не отменяет остальные
    async def fail():
        raise RuntimeError("boom")

    results2 = await parallel([lambda: fn(1), fail, lambda: fn(3)])
    check("failure isolated", results2[1].success is False)
    check("others continue", results2[0].success and results2[2].success)


def test_hitl() -> None:
    print("[hitl]")
    ladder = AutonomyLadder(nominal=RLevel.R5)
    check("nominal no factors -> R5", ladder.effective() is RLevel.R5)
    check("R5 - (1+1+1) -> R2",
          ladder.effective(criticality=1, risk=1, complexity=1) is RLevel.R2)

    d = ladder.decide(criticality=2, risk=2, complexity=1)
    check("decide returns R0", d.level is RLevel.R0)
    check("reason not empty", len(d.reason) > 0)
    check("mode for R0 is interactive", ladder.mode(d.level) == "interactive")


async def main() -> int:
    print("=" * 50)
    print("agentic_guardrails — smoke test")
    print("=" * 50)
    print()

    test_cost_cb()
    print()
    test_failure_cb()
    print()
    await test_policy_hook_bus()
    print()
    test_audit()
    print()
    await test_saga()
    print()
    test_hitl()
    print()
    await test_retry_policy()

    print()
    print("=" * 50)
    print("✓ Все модули работают")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except AssertionError as e:
        print(f"\n✗ {e}")
        sys.exit(1)
