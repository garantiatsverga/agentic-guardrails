"""МОК агентов Scoring / Compliance / Review (спринт 1).

В полной архитектуре:
- Scoring Agent вызывает модель «ВТБ-Скоринг МСБ v3.2» (ML, не LLM)
- Compliance Agent делает MCP-вызовы в ФНС/ЕГРЮЛ/БКИ
- Review Agent формирует финальный вердикт + объяснение для клиента

В спринте 1 все три — **детерминированные моки** со значениями
из примера документа коллеги (заявка МСБ-2026-0231-8847).

См. docs/simplifications.md, строки «Scoring Agent», «Compliance Agent»,
«Review Agent».
"""
from __future__ import annotations

from document_agent.application import CreditApplication


def scoring_agent(application: CreditApplication) -> dict:
    """Scoring Agent: скоринг-балл, риск-категория, риск-факторы.

    МОК. Значения из документа (Раздел 8 «в финале»).
    """
    return {
        "source": "mock",
        "model": "ВТБ-Скоринг МСБ, версия 3.2",
        "score": 685,
        "threshold_auto_approve": 700,
        "threshold_reject": 600,
        "verdict": "ТРЕБУЕТ РАСШИРЕННОГО АНДЕРРАЙТИНГА",
        "risk_category": "Средний риск (категория В)",
        "risk_factors": [
            {"factor": "Стаж предпринимательства 11 лет", "impact": "положительное"},
            {"factor": "Стабильная выручка и обороты", "impact": "положительное"},
            {"factor": "Розничная торговля (среднерисковая отрасль)", "impact": "нейтральное"},
            {"factor": "Запрашиваемая сумма = 45% годовой выручки", "impact": "негативное"},
            {"factor": "Залог покрывает только 60% кредита", "impact": "негативное"},
            {"factor": "Ограниченная кредитная история (1 договор)", "impact": "нейтральное"},
        ],
    }


def compliance_agent(application: CreditApplication) -> dict:
    """Compliance Agent: ФНС/ЕГРЮЛ/БКИ + проверка политик.

    МОК. В полной версии — MCP-вызовы с mTLS (спринт 2).
    """
    return {
        "source": "mock",
        "fns": {"status": "active", "tax_regime": "УСН"},
        "egrul": {"status": "active", "legal_name": "ИП Иванов И.И."},
        "bki": {"status": "good", "active_loans": 1, "overdue": 0},
        "policies": {
            "152-FZ": "passed",
            "115-FZ": "passed",
            "CBR-AML": "passed",
            "CBR-MSB-2024": "passed",
            "OWASP-TOP10": "passed",
        },
        "pdn_masked": True,   # маскировка ПДН — реально в AuditChain
    }


def review_agent(
    application: CreditApplication,
    scoring: dict,
    compliance: dict,
) -> dict:
    """Review Agent: финальный вердикт + объяснение для клиента.

    МОК. В полной версии — структурированное ревью (возможно, LLM
    для генерации текста, но в спринте 1 — шаблон).
    """
    score = scoring["score"]
    auto = scoring["threshold_auto_approve"]
    reject = scoring["threshold_reject"]
    amount = application.requested_amount

    if score >= auto and amount <= 5_000_000:
        decision = "APPROVE"
    elif score < reject:
        decision = "REJECT"
    else:
        decision = "ESCALATE_TO_HITL"

    return {
        "source": "mock",
        "decision": decision,
        "reason": (
            f"Сумма {amount:,} ₽ (порог 5 000 000) + "
            f"скоринг {score} (серая зона {reject}-{auto}) → "
            f"требуется андеррайтинг"
        ),
        "requires_hitl": decision == "ESCALATE_TO_HITL",
    }


def hitl_agent(application: CreditApplication, review: dict) -> dict:
    """HITL: обязательная валидация андеррайтером (R2).

    МОК. В полной версии — Human Task в Temporal + MFA (спринт 2).
    """
    return {
        "source": "mock",
        "required": review["requires_hitl"],
        "r_level": application.r_level or "R2",
        "expected_duration": "до 4 часов",
        "mfa_required": True,
        "status": "PENDING" if review["requires_hitl"] else "SKIPPED",
    }
