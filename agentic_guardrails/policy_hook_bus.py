"""Policy Hook Bus — enforcement правил до исполнения действия.

Правило: policy(action, ctx) -> Decision. Хуки вызываются по порядку,
первый не-ALLOW останавливает цепочку.

Каждое правило ограничено таймаутом (по умолчанию 100 мс).
Зависшее правило приводит к ESCALATE, не блокируя шину.

Два режима прогона:
- check()     — enforcement: остановиться на первом не-ALLOW.
- check_all() — отчёт: прогнать все правила, вернуть все результаты.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

import asyncio


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class PolicyResult:
    decision: Decision
    reason: str = ""
    policy_id: str = ""


Policy = Callable[[str, dict[str, Any]], Awaitable[PolicyResult]]


# Таймаут по умолчанию на одно правило: 100 мс
DEFAULT_TIMEOUT_PER_POLICY = 0.1


@dataclass
class HookBus:
    """Шина правил с таймаутом на каждое правило.

    timeout_per_policy: секунды, по умолчанию 0.1 (100 мс).
        Если правило не успело — возвращается ESCALATE с reason="policy timeout".
        Значение <= 0 отключает таймаут (для отладки).
    """

    timeout_per_policy: float = DEFAULT_TIMEOUT_PER_POLICY
    policies: list[tuple[str, Policy]] = field(default_factory=list)

    def register(self, policy_id: str, policy: Policy) -> None:
        self.policies.append((policy_id, policy))

    async def _run_one(self, pid: str, policy: Policy, action: str, ctx: dict) -> PolicyResult:
        """Запускает одно правило с таймаутом."""
        if self.timeout_per_policy <= 0:
            return await policy(action, ctx)
        try:
            return await asyncio.wait_for(
                policy(action, ctx),
                timeout=self.timeout_per_policy,
            )
        except asyncio.TimeoutError:
            return PolicyResult(
                decision=Decision.ESCALATE,
                reason=f"policy timeout after {self.timeout_per_policy}s",
                policy_id=pid,
            )

    async def check(self, action: str, ctx: dict[str, Any]) -> PolicyResult:
        """Enforcement: останавливается на первом не-ALLOW.

        Возвращает первый не-ALLOW результат, либо ALLOW, если все прошли.
        Подходит для блокировки действия.
        """
        for pid, policy in self.policies:
            result = await self._run_one(pid, policy, action, ctx)
            if result.decision is not Decision.ALLOW:
                return PolicyResult(
                    decision=result.decision,
                    reason=result.reason,
                    policy_id=result.policy_id or pid,
                )
        return PolicyResult(Decision.ALLOW)

    async def check_all(self, action: str, ctx: dict[str, Any]) -> list[PolicyResult]:
        """Отчёт: прогоняет ВСЕ правила, не останавливаясь на первом не-ALLOW.

        Возвращает список результатов в порядке регистрации правил.
        Нужен для отчётов валидации (в отличие от check(), который
        останавливает цепочку при первом не-ALLOW).
        """
        results: list[PolicyResult] = []
        for pid, policy in self.policies:
            result = await self._run_one(pid, policy, action, ctx)
            results.append(PolicyResult(
                decision=result.decision,
                reason=result.reason,
                policy_id=result.policy_id or pid,
            ))
        return results
