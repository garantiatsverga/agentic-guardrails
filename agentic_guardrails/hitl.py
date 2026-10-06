"""Adaptive Autonomy — лестница R0–R5.

Effective R = Nominal R - (criticality + risk + complexity)
Человек вовлекается только там, где цена ошибки высока.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import IntEnum
from typing import Awaitable, Callable


class RLevel(IntEnum):
    R0 = 0  # интерактивный HITL
    R1 = 1
    R2 = 2  # HITL по исключению
    R3 = 3  # batch
    R4 = 4
    R5 = 5  # HOTL, только аудит


@dataclass
class AutonomyLadder:
    nominal: RLevel

    def effective(
        self,
        criticality: int = 0,
        risk: int = 0,
        complexity: int = 0,
    ) -> RLevel:
        delta = criticality + risk + complexity
        return RLevel(max(0, int(self.nominal) - delta))

    def mode(self, level: RLevel) -> str:
        if level <= RLevel.R1:
            return "interactive"
        if level == RLevel.R2:
            return "on_exception"
        if level <= RLevel.R4:
            return "batch"
        return "autonomous"


ApprovalFn = Callable[[dict], Awaitable[bool]]


async def run_with_approval(
    ladder: AutonomyLadder,
    action: dict,
    approve: ApprovalFn,
    ctx: dict | None = None,
) -> bool:
    """Возвращает True если действие разрешено."""
    ctx = ctx or {}
    level = ladder.effective(
        criticality=ctx.get("criticality", 0),
        risk=ctx.get("risk", 0),
        complexity=ctx.get("complexity", 0),
    )
    mode = ladder.mode(level)

    if mode == "autonomous":
        return True
    if mode == "batch":
        return await approve({**action, "_batch": True})
    return await approve(action)
