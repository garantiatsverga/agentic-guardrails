"""Policy Hook Bus — enforcement правил до исполнения действия.

Правило: policy(action) -> Decision. Хуки вызываются по порядку,
первый не-ALLOW останавливает цепочку.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable


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


@dataclass
class HookBus:
    policies: list[tuple[str, Policy]] = field(default_factory=list)

    def register(self, policy_id: str, policy: Policy) -> None:
        self.policies.append((policy_id, policy))

    async def check(self, action: str, ctx: dict[str, Any]) -> PolicyResult:
        for pid, policy in self.policies:
            result = await policy(action, ctx)
            if result.decision is not Decision.ALLOW:
                return PolicyResult(
                    decision=result.decision,
                    reason=result.reason,
                    policy_id=result.policy_id or pid,
                )
        return PolicyResult(Decision.ALLOW)
