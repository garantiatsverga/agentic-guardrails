"""МОК Оркестратора (спринт 1).

В полной архитектуре (АР Оркестратор §2.2, §3.1) Оркестратор:
- создаёт workflow_id, task_id, session_id
- вычисляет R-level через триаду «критичность × риск × сложность»
- назначает token_budget на основе Pattern Library и R-level
- определяет policy_ids по типу заявки и R-level
- строит маршрут декомпозиции

В спринте 1 всё это — **детерминированные значения** из примера
документа коллеги (заявка МСБ-2026-0231-8847). Реальный Оркестратор
(Temporal) — спринт 2–3.

См. docs/simplifications.md, строка «Оркестратор».
"""
from __future__ import annotations

import uuid

from document_agent.application import CreditApplication


def discovery(application: CreditApplication) -> dict:
    """Discovery-фаза: метаданные, R-level, бюджет, политики.

    МОК. Значения — из документа коллеги (Раздел 1 «в финале»).
    """
    app_short = application.application_id[:8] if application.application_id else "7f3a9c21"
    return {
        "source": "mock",
        "workflow_id": f"wf_msb_credit_{app_short}_v1",
        "task_id": f"task_20260221_104201_{app_short}",
        "session_id": "sess_88471042_20260221",
        "spec_pointer": "sdd-registry://msb-credit-processing/v2.3",
        # R2: сумма 5 млн = пограничная → эскалация
        # (в полной версии — триада «критичность × риск × сложность»)
        "r_level": "R2",
        "token_budget": {
            "per_task": 500_000,
            "per_session": 150_000,
            "per_horizon": 1_000_000,
        },
        "policy_ids": [
            "152-FZ", "CBR-AML", "115-FZ", "CBR-MSB-2024", "OWASP-TOP10",
        ],
    }


def build_route(application: CreditApplication) -> list[dict]:
    """Маршрут обработки — 8 этапов (Раздел 10 «в финале»).

    МОК. Статусы: выполнено / в очереди / ожидает.
    """
    return [
        {"num": 1, "agent": "Оркестратор",
         "action": "Декомпозиция заявки, назначение R-level, создание Workflow",
         "status": "выполнено"},
        {"num": 2, "agent": "Document Agent",
         "action": "Извлечение данных из документов (анкета, паспорт, выписки)",
         "status": "в очереди"},
        {"num": 3, "agent": "Scoring Agent",
         "action": "Вызов скоринг-модели, интерпретация результата",
         "status": "ожидает этапа 2"},
        {"num": 4, "agent": "Compliance Agent",
         "action": "Проверка 152-ФЗ, 115-ФЗ, AML, ФНС/ЕГРЮЛ",
         "status": "параллельно с этапом 3"},
        {"num": 5, "agent": "Review Agent",
         "action": "Структурированное ревью решения",
         "status": "ожидает этапов 3-4"},
        {"num": 6, "agent": "Андеррайтер (HITL)",
         "action": "Обязательная валидация (сумма = 5 млн, скоринг 685)",
         "status": "ожидает этапа 5"},
        {"num": 7, "agent": "Оркестратор",
         "action": "Агрегация результатов, формирование Evidence Bundle",
         "status": "ожидает этапа 6"},
        {"num": 8, "agent": "ВТБ-Бизнес Онлайн",
         "action": "Возврат решения менеджеру и клиенту",
         "status": "финал"},
    ]


def update_route_status(route: list[dict], agent: str, status: str) -> None:
    """Обновляет статус этапа по имени агента (для UI после прогона)."""
    for step in route:
        if step["agent"] == agent:
            step["status"] = status
