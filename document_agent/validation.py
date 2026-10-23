"""Валидация извлечённых данных (спринт 1, §5 шаг 4, §6.1).

Правила — через HookBus (agentic_guardrails.policy_hook_bus).
Прогоняем check_all(), чтобы получить ВСЕ результаты.

Правила:
- required_fields   — обязательные поля присутствуют (§4.1)
- inn_format        — ИНН 10 цифр
- amount_positive   — сумма > 0
- term_positive     — срок > 0
- amount_large      — сумма > 5 млн -> ESCALATE (§9.1)
- field_conflict    — поле встретилось в 2+ документах с разными
                      значениями -> ESCALATE (warning). Конфликты
                      передаются через ctx["_conflicts"].
"""
from __future__ import annotations

from agentic_guardrails.policy_hook_bus import (
    HookBus, PolicyResult, Decision,
)

from document_agent.models import ExtractedData, ValidationResult


async def _rule_required_fields(action: str, ctx: dict) -> PolicyResult:
    data = ExtractedData(**{k: v for k, v in ctx.items() if not k.startswith("_")})
    missing = data.missing_fields()
    if missing:
        return PolicyResult(
            Decision.DENY,
            reason=f"Отсутствуют обязательные поля: {', '.join(missing)}",
            policy_id="required_fields",
        )
    return PolicyResult(Decision.ALLOW, policy_id="required_fields")


async def _rule_inn_format(action: str, ctx: dict) -> PolicyResult:
    inn = ctx.get("inn")
    if inn is None:
        return PolicyResult(Decision.ALLOW, policy_id="inn_format")
    if not isinstance(inn, str) or len(inn) != 10 or not inn.isdigit():
        return PolicyResult(
            Decision.DENY,
            reason=f"ИНН должен быть 10 цифр, получено: {inn!r}",
            policy_id="inn_format",
        )
    return PolicyResult(Decision.ALLOW, policy_id="inn_format")


async def _rule_amount_positive(action: str, ctx: dict) -> PolicyResult:
    amount = ctx.get("requested_amount")
    if amount is None:
        return PolicyResult(Decision.ALLOW, policy_id="amount_positive")
    if amount <= 0:
        return PolicyResult(
            Decision.DENY,
            reason=f"Сумма должна быть > 0, получено: {amount}",
            policy_id="amount_positive",
        )
    return PolicyResult(Decision.ALLOW, policy_id="amount_positive")


async def _rule_term_positive(action: str, ctx: dict) -> PolicyResult:
    term = ctx.get("requested_term_months")
    if term is None:
        return PolicyResult(Decision.ALLOW, policy_id="term_positive")
    if term <= 0:
        return PolicyResult(
            Decision.DENY,
            reason=f"Срок должен быть > 0, получено: {term}",
            policy_id="term_positive",
        )
    return PolicyResult(Decision.ALLOW, policy_id="term_positive")


async def _rule_amount_large(action: str, ctx: dict) -> PolicyResult:
    """Сумма > 5 млн -> ESCALATE (§9.1)."""
    amount = ctx.get("requested_amount")
    if amount is not None and amount > 5_000_000:
        return PolicyResult(
            Decision.ESCALATE,
            reason=f"Сумма {amount} > 5 000 000, требуется андеррайтер",
            policy_id="amount_large",
        )
    return PolicyResult(Decision.ALLOW, policy_id="amount_large")


async def _rule_field_conflict(action: str, ctx: dict) -> PolicyResult:
    """Конфликт между документами -> ESCALATE (warning).

    Конфликты приходят из extraction.extract_bundle() через
    ctx["_conflicts"]. Если их нет — правило пропускает.
    """
    conflicts = ctx.get("_conflicts") or []
    if not conflicts:
        return PolicyResult(Decision.ALLOW, policy_id="field_conflict")

    parts = []
    for c in conflicts:
        values_str = ", ".join(f"{v} в {src}" for src, v in c["values"].items())
        parts.append(
            f"{c['field']}: {values_str}. "
            f"Взято {c['chosen']} из {c['chosen_source']}"
        )
    return PolicyResult(
        Decision.ESCALATE,
        reason="Конфликты между документами: " + "; ".join(parts),
        policy_id="field_conflict",
    )


def build_validation_bus() -> HookBus:
    """Собирает шину правил валидации."""
    bus = HookBus(timeout_per_policy=0.1)
    bus.register("required_fields", _rule_required_fields)
    bus.register("inn_format", _rule_inn_format)
    bus.register("amount_positive", _rule_amount_positive)
    bus.register("term_positive", _rule_term_positive)
    bus.register("amount_large", _rule_amount_large)
    bus.register("field_conflict", _rule_field_conflict)
    return bus


_DECISION_TO_STATUS = {
    Decision.ALLOW: "passed",
    Decision.DENY: "error",
    Decision.ESCALATE: "warning",
}


def to_validation_results(
    policy_results: list[PolicyResult],
) -> list[ValidationResult]:
    """PolicyResult[] -> ValidationResult[] по §6.1."""
    return [
        ValidationResult(
            field=r.policy_id,
            status=_DECISION_TO_STATUS[r.decision],
            message=r.reason,
        )
        for r in policy_results
    ]
