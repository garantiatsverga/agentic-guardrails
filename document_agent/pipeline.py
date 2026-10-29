"""Pipeline обработки документов (спринт 1, §5 шаги 1–5)."""
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
    """Полный прогон Document Agent: извлечение -> валидация -> эскалация."""
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


# ============================================================================
# Полный процесс (вариант B): 8 этапов по документу коллеги
# ============================================================================

async def run_full_process(
    files: list,
    business_description: str = "",
) -> "CreditApplication":
    """Полный процесс заявки: IDP → Оркестратор → агенты → HITL → Evidence.

    Реально: Document Agent (шаг 2) + AuditChain + EvidenceBundle.
    МОК: Оркестратор, Scoring, Compliance, Review, HITL.

    Возвращает CreditApplication со всеми разделами.
    """
    from document_agent.application import CreditApplication
    from document_agent.agents_mock import (
        compliance_agent, hitl_agent, review_agent, scoring_agent,
    )
    from document_agent.orchestrator_mock import (
        build_route, discovery, update_route_status,
    )

    application = CreditApplication()
    application.application_id = str(uuid.uuid4())
    application.idempotency_key = f"{application.application_id}_attempt_001"

    _audit.append("idp.received", {
        "app_id": application.application_id,
        "channel": application.channel,
        "manager": application.manager,
    })

    # ==== Шаг 3: Discovery (МОК) ====
    disc = discovery(application)
    application.workflow_id = disc["workflow_id"]
    application.task_id = disc["task_id"]
    application.session_id = disc["session_id"]
    application.r_level = disc["r_level"]
    application.token_budget = disc["token_budget"]
    application.policy_ids = disc["policy_ids"]
    application.spec_pointer = disc["spec_pointer"]
    application.audit_pointer = f"audit/2026/02/21/{application.session_id}.jsonl"

    _audit.append("orchestrator.discovery", {
        "app_id": application.application_id,
        "workflow_id": application.workflow_id,
        "r_level": application.r_level,
        "policy_ids": application.policy_ids,
        "source": "mock",
    })

    # ==== Шаг 4: Декомпозиция (МОК) ====
    application.route = build_route(application)
    update_route_status(application.route, "Оркестратор", "выполнено")

    # ==== Шаг 5.1: Document Agent (РЕАЛЬНО) ====
    update_route_status(application.route, "Document Agent", "выполняется")
    application.document_agent_result = await run_pipeline(
        files, business_description,
    )
    update_route_status(
        application.route, "Document Agent",
        "выполнено" if application.document_agent_result.extraction_status != "failed"
        else "ошибка",
    )

    _audit.append("agent.document.completed", {
        "app_id": application.application_id,
        "status": application.document_agent_result.extraction_status,
        "source": "real",
    })

    # ==== Шаг 5.2: Scoring (МОК) ====
    application.scoring_result = scoring_agent(application)
    update_route_status(application.route, "Scoring Agent", "выполнено (МОК)")
    _audit.append("agent.scoring.completed", {
        "app_id": application.application_id,
        "score": application.scoring_result["score"],
        "source": "mock",
    })

    # ==== Шаг 5.3: Compliance (МОК) ====
    application.compliance_result = compliance_agent(application)
    update_route_status(application.route, "Compliance Agent", "выполнено (МОК)")
    _audit.append("agent.compliance.completed", {
        "app_id": application.application_id,
        "policies": list(application.compliance_result["policies"].keys()),
        "source": "mock",
    })

    # ==== Шаг 5.4: Review (МОК) ====
    application.review_result = review_agent(
        application, application.scoring_result, application.compliance_result,
    )
    update_route_status(application.route, "Review Agent", "выполнено (МОК)")

    # ==== Шаг 6: HITL (МОК) ====
    application.hitl_result = hitl_agent(application, application.review_result)
    update_route_status(
        application.route, "Андеррайтер (HITL)",
        "ожидает" if application.hitl_result["required"] else "не требуется",
    )

    # ==== Шаг 7: Evidence Bundle (реально AuditChain) ====
    application.evidence_bundle_ref = (
        f"s3://vtb-msb-evidence/2026/02/21/"
        f"{application.application_id[:8]}/bundle_001.tar.gz"
    )
    update_route_status(application.route, "Оркестратор", "выполнено")

    application.final_status = "ПРИНЯТА_К_ОБРАБОТКЕ"

    _audit.append("workflow.completed", {
        "app_id": application.application_id,
        "r_level": application.r_level,
        "audit_ok": _audit.verify(),
    })

    return application


# ==== Внутренние ====

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
