"""Тесты Document Agent (спринт 1).

Уровни:
- L1 (unit):       extraction, validation, escalation — по отдельности
- L2 (integration): pipeline на 4 кейсах §12 + пакет + конфликт
- L3 (HTTP):        FastAPI TestClient — форма, JSON §6.1, эскалация, аудит

Запуск:
    pytest tests/test_document_agent.py -v
    pytest tests/test_document_agent.py -v -k "case"
"""
from __future__ import annotations

from pathlib import Path

import pytest

from document_agent.extraction import (
    ExtractionFailed, extract_bundle, extract_from_pdf, parse_text,
)
from document_agent.models import ExtractedData
from document_agent.pipeline import get_audit_chain, reset_state, run_pipeline
from document_agent.validation import build_validation_bus, to_validation_results


FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _reset_pipeline_state():
    """Каждый тест — с чистым AuditChain и FailureBreaker."""
    reset_state()
    yield
    reset_state()


# ============================================================================
# L1: extraction
# ============================================================================

class TestExtraction:
    def test_parse_text_basic(self):
        text = (
            "Название компании: ООО Ромашка\n"
            "ИНН: 1234567890\n"
            "Запрашиваемая сумма: 1000000\n"
        )
        fields = parse_text(text)
        assert fields["company_name"] == "ООО Ромашка"
        assert fields["inn"] == "1234567890"
        assert fields["requested_amount"] == 1000000

    def test_parse_text_ignores_unknown(self):
        fields = parse_text("Что-то непонятное: значение\nИНН: 1234567890\n")
        assert fields == {"inn": "1234567890"}

    def test_parse_text_no_colon(self):
        fields = parse_text("просто строка без двоеточия\n")
        assert fields == {}

    def test_extract_from_pdf_case1(self):
        data = extract_from_pdf(FIXTURES / "case1_success.pdf")
        assert data.company_name == "ООО Ромашка"
        assert data.inn == "1234567890"
        assert data.requested_amount == 1000000
        assert data.missing_fields() == []

    def test_extract_from_pdf_case2_missing_inn(self):
        data = extract_from_pdf(FIXTURES / "case2_partial.pdf")
        assert data.inn is None
        assert data.missing_fields() == ["inn"]

    def test_extract_from_pdf_case4_broken(self):
        with pytest.raises(Exception):
            extract_from_pdf(FIXTURES / "case4_failed.pdf")

    def test_extract_bundle_roles(self):
        bundle = extract_bundle(
            [FIXTURES / "package_anketa.pdf", FIXTURES / "package_financial.pdf"],
            "Производство мебели",   # форма заполняет обязательное поле §4.1
        )
        assert bundle.roles["package_anketa.pdf"] == "application"
        assert bundle.roles["package_financial.pdf"] == "financial"
        assert bundle.status == "success"
        assert bundle.conflicts == []

    def test_extract_bundle_priority(self):
        """Анкета — приоритетный источник для requested_amount (§4.1)."""
        bundle = extract_bundle([
            FIXTURES / "conflict_financial.pdf",
            FIXTURES / "conflict_anketa.pdf",   # порядок намеренно обратный
        ])
        assert bundle.data.requested_amount == 1000000    # из анкеты
        assert bundle.sources["requested_amount"] == "conflict_anketa.pdf"

    def test_extract_bundle_conflict_detected(self):
        bundle = extract_bundle([
            FIXTURES / "conflict_anketa.pdf",
            FIXTURES / "conflict_financial.pdf",
        ])
        assert len(bundle.conflicts) == 1
        c = bundle.conflicts[0]
        assert c["field"] == "requested_amount"
        assert c["chosen"] == 1000000
        assert c["chosen_source"] == "conflict_anketa.pdf"

    def test_extract_bundle_all_broken(self):
        with pytest.raises(ExtractionFailed):
            extract_bundle([FIXTURES / "case4_failed.pdf"])

    def test_business_description_form_overrides_pdf(self):
        bundle = extract_bundle(
            [FIXTURES / "package_anketa.pdf"],
            business_description="Розничная торговля",
        )
        assert bundle.data.business_description == "Розничная торговля"
        assert bundle.sources["business_description"] == "<form>"


# ============================================================================
# L1: validation
# ============================================================================

class TestValidation:
    async def test_all_passed_on_case1(self):
        data = ExtractedData(
            company_name="ООО Ромашка", inn="1234567890",
            requested_amount=1_000_000, requested_term_months=24,
            business_description="Мебель",
        )
        bus = build_validation_bus()
        results = await bus.check_all("validate", data.model_dump())
        results = to_validation_results(results)
        for r in results:
            assert r.status == "passed", f"{r.field}: {r.message}"

    async def test_inn_format_error(self):
        data = ExtractedData(
            company_name="X", inn="123", requested_amount=1000,
            requested_term_months=12, business_description="Y",
        )
        bus = build_validation_bus()
        results = to_validation_results(
            await bus.check_all("validate", data.model_dump())
        )
        inn = next(r for r in results if r.field == "inn_format")
        assert inn.status == "error"

    async def test_amount_large_warning(self):
        data = ExtractedData(
            company_name="X", inn="1234567890", requested_amount=10_000_000,
            requested_term_months=12, business_description="Y",
        )
        bus = build_validation_bus()
        results = to_validation_results(
            await bus.check_all("validate", data.model_dump())
        )
        large = next(r for r in results if r.field == "amount_large")
        assert large.status == "warning"

    async def test_field_conflict_warning(self):
        ctx = {
            "company_name": "X", "inn": "1234567890",
            "requested_amount": 1000, "requested_term_months": 12,
            "business_description": "Y",
            "_conflicts": [{
                "field": "requested_amount",
                "values": {"a.pdf": 1000, "b.pdf": 2000},
                "chosen": 1000, "chosen_source": "a.pdf",
            }],
        }
        bus = build_validation_bus()
        results = to_validation_results(await bus.check_all("validate", ctx))
        conflict = next(r for r in results if r.field == "field_conflict")
        assert conflict.status == "warning"
        assert "requested_amount" in conflict.message


# ============================================================================
# L2: pipeline — 4 кейса §12
# ============================================================================

@pytest.mark.parametrize("case,expected_status,expected_review", [
    ("case1_success.pdf",  "success", False),
    ("case2_partial.pdf",  "partial", True),
    ("case3_escalate.pdf", "success", True),
    ("case4_failed.pdf",   "failed",  True),
])
async def test_four_cases_s12(case, expected_status, expected_review):
    """4 кейса из §12 спеки."""
    result = await run_pipeline([FIXTURES / case], "")
    assert result.extraction_status == expected_status
    assert result.requires_manual_review == expected_review


async def test_case1_json_matches_spec():
    """model_dump() — ровно структура §6.1."""
    result = await run_pipeline([FIXTURES / "case1_success.pdf"], "Мебель")
    data = result.model_dump()

    # Корневые поля §6.1
    assert set(data.keys()) == {
        "application_id", "extraction_status", "extracted_data",
        "validation_results", "missing_data",
        "requires_manual_review", "escalation_reason",
    }
    # extracted_data — все 8 полей
    assert set(data["extracted_data"].keys()) == {
        "company_name", "inn", "requested_amount", "requested_term_months",
        "revenue_last_year", "profit_last_year", "business_description",
        "employees_count",
    }
    assert data["extraction_status"] == "success"
    assert data["missing_data"] == []


async def test_case2_missing_inn():
    result = await run_pipeline([FIXTURES / "case2_partial.pdf"], "")
    assert result.extraction_status == "partial"
    assert "inn" in result.missing_data
    assert result.escalation_reason
    assert "inn" in result.escalation_reason


async def test_case3_escalation_reason():
    result = await run_pipeline([FIXTURES / "case3_escalate.pdf"], "")
    assert result.requires_manual_review is True
    assert result.escalation_reason
    assert "5000000" in result.escalation_reason


async def test_case4_failed():
    result = await run_pipeline([FIXTURES / "case4_failed.pdf"], "")
    assert result.extraction_status == "failed"
    assert result.requires_manual_review is True
    assert len(result.missing_data) >= 5


async def test_package_no_conflicts():
    """Анкета + финотчётность одной заявки -> success, конфликтов нет."""
    result = await run_pipeline(
        [FIXTURES / "package_anketa.pdf", FIXTURES / "package_financial.pdf"],
        "Производство мебели",   # форма заполняет обязательное поле
    )
    assert result.extraction_status == "success"
    conflict = next(
        v for v in result.validation_results if v.field == "field_conflict"
    )
    assert conflict.status == "passed"


async def test_conflict_detected_and_escalated():
    """Разные суммы в двух документах -> warning + эскалация."""
    result = await run_pipeline(
        [FIXTURES / "conflict_anketa.pdf", FIXTURES / "conflict_financial.pdf"],
        "",
    )
    conflict = next(
        v for v in result.validation_results if v.field == "field_conflict"
    )
    assert conflict.status == "warning"
    assert "requested_amount" in conflict.message
    assert result.requires_manual_review is True


async def test_no_files_fails():
    result = await run_pipeline([], "")
    assert result.extraction_status == "failed"
    assert "ни одного файла" in (result.escalation_reason or "")


async def test_audit_verify_after_pipeline():
    await run_pipeline([FIXTURES / "case1_success.pdf"], "Мебель")
    await run_pipeline([FIXTURES / "case2_partial.pdf"], "")
    chain = get_audit_chain()
    assert chain.verify() is True
    assert len(chain) > 0


async def test_failure_cb_opens_after_three_broken():
    """3 битых PDF подряд -> FailureBreaker открывается (CLOSED -> OPEN)."""
    from agentic_guardrails.failure_cb import CBState
    for _ in range(3):
        await run_pipeline([FIXTURES / "case4_failed.pdf"], "")
    # Четвёртый вызов должен быть заблокирован CB
    result = await run_pipeline([FIXTURES / "case4_failed.pdf"], "")
    assert "circuit open" in (result.escalation_reason or "").lower()


# ============================================================================
# L3: HTTP (FastAPI TestClient)
# ============================================================================

class TestWeb:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from document_agent.web.app import app
        # очищаем in-memory результаты между тестами
        from document_agent.web import app as app_module
        app_module.RESULTS.clear()
        app_module.ESCALATIONS.clear()
        return TestClient(app)

    def test_index_renders(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "Загрузка документов" in r.text

    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_upload_case1(self, client):
        with open(FIXTURES / "case1_success.pdf", "rb") as f:
            r = client.post(
                "/upload",
                files=[("files", ("case1_success.pdf", f, "application/pdf"))],
                data={"business_description": "Мебель"},
                follow_redirects=False,
            )
        assert r.status_code == 303
        location = r.headers["location"]
        assert location.startswith("/result/")

        # JSON §6.1
        app_id = location.rsplit("/", 1)[-1]
        j = client.get(f"/result/{app_id}.json")
        assert j.status_code == 200
        data = j.json()
        assert data["extraction_status"] == "success"
        assert data["extracted_data"]["company_name"] == "ООО Ромашка"

    def test_upload_rejects_wrong_extension(self, client):
        r = client.post(
            "/upload",
            files=[("files", ("evil.exe", b"MZ", "application/octet-stream"))],
            data={"business_description": ""},
        )
        assert r.status_code == 400
        assert "Недопустимый формат" in r.text

    def test_upload_rejects_no_files(self, client):
        r = client.post(
            "/upload",
            data={"business_description": ""},
        )
        assert r.status_code == 400
        assert "хотя бы один файл" in r.text

    def test_upload_rejects_too_large(self, client):
        big = b"x" * (10 * 1024 * 1024 + 1)
        r = client.post(
            "/upload",
            files=[("files", ("big.pdf", big, "application/pdf"))],
            data={"business_description": ""},
        )
        assert r.status_code == 400
        assert "слишком большой" in r.text

    def test_escalate(self, client):
        with open(FIXTURES / "case1_success.pdf", "rb") as f:
            r = client.post(
                "/upload",
                files=[("files", ("case1_success.pdf", f, "application/pdf"))],
                data={"business_description": ""},
                follow_redirects=False,
            )
        app_id = r.headers["location"].rsplit("/", 1)[-1]

        r2 = client.post(
            f"/result/{app_id}/escalate",
            data={"reason": "Проверить вручную"},
            follow_redirects=False,
        )
        assert r2.status_code == 303
        # В аудите появилась запись
        chain = get_audit_chain()
        events = [e.event for e in chain.entries]
        assert "escalation.requested" in events

    def test_audit_page_verify(self, client):
        client.get("/")  # любой запрос, чтобы приложение поднялось
        r = client.get("/audit")
        assert r.status_code == 200
        assert "verify()" in r.text
