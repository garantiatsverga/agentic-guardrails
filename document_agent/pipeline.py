"""Pipeline обработки документов (спринт 1, §5 шаги 1–5).

Шаги:
1. Загрузка (проверка формата/размера — на уровне web/app.py)
2. Извлечение (extraction.extract_bundle + FailureBreaker §8.2)
3. Структурирование (уже в extraction)
4. Валидация (validation через HookBus.check_all + field_conflict)
5. Результат или эскалация (escalation.decide_escalation)

Пакет 1–3 файлов одной заявки:
- роль каждого документа (application / financial / description)
- слияние с приоритетом по §4.1
- конфликты между документами -> warning в validation_results
"""
from __future__ import annotations

import uuid

from agentic_guardrails.audit import AuditChain
from agentic_guardrails.evidence import EvidenceBundle
from agentic_guardrails.failure_cb import FailureBreaker

from document_agent.escalation import decide_escalation
from document_agent.extraction import ExtractionFailed, extract_bundle
from document_agent.mocks import collect_external
from document_agent.models import ApplicationResult, ExtractedData
from document_agent.validation import build_validation_bus, to_validation_results


_audit = AuditChain()
_extraction_cb = FailureBreaker(failure_threshold=3, recovery_timeout=120.0)
_validation_bus = build_validation_bus()


def get_audit_chain() -> AuditChain:
    return _audit


def reset_state() -> None:
    global _audit, _extraction_cb, _validation_bus
    _audit = AuditChain()
    _extraction_cb = FailureBreaker(failure_threshold=3, recovery_timeout=120.0)
    _validation_bus = build_validation_bus()


async def run_pipeline(
    files: list,
    business_description: str = "",
) -> ApplicationResult:
    """Полный прогон: извлечение -> валидация -> эскалация -> результат."""
    app_id = str(uuid.uuid4())

    _audit.append("application.received", {
        "app_id": app_id,
        "files": len(files),
    })

    if not files:
        _audit.append("extraction.skipped", {"app_id": app_id, "reason": "no files"})
        return _finish_failed(app_id, "Не загружено ни одного файла")

    if not _extraction_cb.allow():
        _audit.append("extraction.blocked", {
            "app_id": app_id, "reason": "circuit open",
        })
        return _finish_failed(
            app_id,
            "Автоматическая обработка недоступна: circuit open. "
            "Заявка передана на ручную проверку.",
        )

    try:
        bundle = extract_bundle(files, business_description)
        _extraction_cb.on_success()
    except ExtractionFailed as exc:
        _extraction_cb.on_failure()
        _audit.append("extraction.failed", {
            "app_id": app_id, "reason": str(exc),
            "cb_state": _extraction_cb.state.value,
        })
        return _finish_failed(app_id, f"Не удалось извлечь данные: {exc}")
    except Exception as exc:
        _extraction_cb.on_failure()
        _audit.append("extraction.error", {
            "app_id": app_id, "error": str(exc),
            "error_type": type(exc).__name__,
            "cb_state": _extraction_cb.state.value,
        })
        return _finish_failed(app_id, f"Ошибка извлечения: {exc}")

    _audit.append("extraction.done", {
        "app_id": app_id,
        "status": bundle.status,
        "sources": bundle.sources,
        "roles": bundle.roles,
        "conflicts": bundle.conflicts,
    })

    # Конфликты передаём в валидацию через контекст
    validation_ctx = {**bundle.data.model_dump(), "_conflicts": bundle.conflicts}
    policy_results = await _validation_bus.check_all("validate", validation_ctx)
    validation_results = to_validation_results(policy_results)
    _audit.append("validation.done", {
        "app_id": app_id,
        "results": [v.model_dump() for v in validation_results],
    })

    requires_review, reason = decide_escalation(
        extraction_status=bundle.status,
        data=bundle.data,
        validation_results=validation_results,
        requested_amount=bundle.data.requested_amount,
    )

    # Если конфликт есть, но decide_escalation не поймал — добавляем причину
    if bundle.conflicts and requires_review:
        conflict_note = (
            f"Конфликты между документами: "
            f"{', '.join(c['field'] for c in bundle.conflicts)}"
        )
        reason = f"{reason}. {conflict_note}" if reason else conflict_note

    bundle_ev = _build_bundle(
        app_id, bundle.status, bundle.data, validation_results,
        requires_review, reason, bundle.conflicts,
    )
    _audit.append("evidence.complete", {
        "app_id": app_id,
        "audit_ok": _audit.verify(),
        "bundle": bundle_ev.to_json(),
    })

    external = collect_external(bundle.data.inn) if bundle.status != "failed" else {}
    _audit.append("external.mocks", {
        "app_id": app_id,
        "sources": list(external.keys()),
    })

    return ApplicationResult(
        application_id=app_id,
        extraction_status=bundle.status,
        extracted_data=bundle.data,
        validation_results=validation_results,
        missing_data=bundle.data.missing_fields(),
        requires_manual_review=requires_review,
        escalation_reason=reason,
    )


def _build_bundle(app_id, status, data, validation_results, requires_review, reason, conflicts):
    outcome = "escalated" if requires_review else (
        "success" if status == "success" else "failure"
    )
    b = EvidenceBundle(task_id=app_id, agent_id="document_agent", outcome=outcome)
    for v in validation_results:
        b.add_validation(v.field, v.status, v.message)
    b.add_decision(
        "final",
        "ESCALATE" if requires_review else "ALLOW",
        reason or "all checks passed",
    )
    b.missing_data = data.missing_fields()
    return b


def _finish_failed(app_id: str, reason: str) -> ApplicationResult:
    _audit.append("application.failed", {"app_id": app_id, "reason": reason})
    return ApplicationResult.failed(app_id, reason)
