"""Эскалация на ручную проверку (спринт 1, §9.1).

Три триггера из спеки:
1. extraction_status: "failed"         -> «Не удалось извлечь данные»
2. Отсутствуют обязательные поля        -> «Отсутствуют данные: [...]»
3. Сумма > 5 млн                        -> «Крупная сумма, требуется андеррайтер»

Решение принимается через AutonomyLadder (agentic_guardrails.hitl).
"""
from __future__ import annotations

from agentic_guardrails.hitl import AutonomyLadder, RLevel

from document_agent.models import ExtractedData, ValidationResult


_LADDER = AutonomyLadder(nominal=RLevel.R5, approval_timeout=30.0)
LARGE_AMOUNT = 5_000_000


def decide_escalation(
    extraction_status: str,
    data: ExtractedData,
    validation_results: list[ValidationResult],
    requested_amount: int | None = None,
) -> tuple[bool, str | None]:
    """Возвращает (requires_manual_review, escalation_reason)."""
    if extraction_status == "failed":
        return True, "Не удалось извлечь данные из документов"

    missing = data.missing_fields()
    if missing:
        return True, f"Отсутствуют данные: {', '.join(missing)}"

    amount = requested_amount if requested_amount is not None else data.requested_amount
    if amount is not None and amount > LARGE_AMOUNT:
        decision = _LADDER.decide(criticality=0, risk=1, complexity=0)
        if decision.level < RLevel.R5:
            return True, (
                f"Крупная сумма ({amount} > {LARGE_AMOUNT}), "
                f"требуется андеррайтер (уровень автономии: {decision.level.name})"
            )

    return False, None
