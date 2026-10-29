"""Тесты Document Agent (спринт 1).

Уровни:
- L1 (unit):       парсинг полей, роли, слияние, валидация.
- L2 (integration): pipeline на 4 кейсах §12 + пакет + реальные документы.
- L3 (HTTP):        FastAPI TestClient — форма, JSON §6.1, эскалация, аудит.

Запуск:
    pytest tests/test_document_agent.py -v
    pytest tests/test_document_agent.py -v -k "case"
"""
from __future__ import annotations

from pathlib import Path

import pytest

from document_agent.extraction import (
    ExtractionFailed, extract_bundle, extract_from_pdf, parse_text,
    FIELD_MAP, ROLE_APPLICATION, ROLE_FINANCIAL, ROLE_UNKNOWN,
)
from document_agent.models import ExtractedData
from document_agent.pipeline import (
    get_audit_chain, reset_state, run_full_process, run_pipeline,
)
from document_agent.validation import build_validation_bus, to_validation_results


FIXTURES = Path(__file__).resolve().parent / "fixtures"
REAL_DOCS = FIXTURES / "real_docs"


@pytest.fixture(autouse=True)
def _reset_pipeline_state():
    reset_state()
    yield
    reset_state()


# ============================================================================
# L1: парсинг каждого поля по отдельности
# ============================================================================

class TestParseEachField:
    """Проверяет, что каждая русская подпись маппится в правильное поле."""

    @pytest.mark.parametrize("raw_key,expected_field", [
        ("Название компании", "company_name"),
        ("ФИО", "company_name"),
        ("Фамилия", "company_name"),
        ("Имя", "company_name"),
        ("Отчество", "company_name"),
        ("ИНН", "inn"),
        ("Запрашиваемая сумма", "requested_amount"),
        ("Срок (мес.)", "requested_term_months"),
        ("Срок", "requested_term_months"),
        ("Выручка за последний год", "revenue_last_year"),
        ("Прибыль за последний год", "profit_last_year"),
        ("Количество сотрудников", "employees_count"),
        ("Описание бизнеса", "business_description"),
    ])
    def test_field_map_contains(self, raw_key, expected_field):
        assert FIELD_MAP.get(raw_key.lower()) == expected_field

    def test_parse_company_name(self):
        fields = parse_text("Название компании: ООО Ромашка")
        assert fields["company_name"] == "ООО Ромашка"

    def test_parse_inn(self):
        fields = parse_text("ИНН: 1234567890")
        assert fields["inn"] == "1234567890"

    def test_parse_requested_amount(self):
        fields = parse_text("Запрашиваемая сумма: 1000000")
        assert fields["requested_amount"] == 1000000

    def test_parse_requested_term(self):
        fields = parse_text("Срок (мес.): 24")
        assert fields["requested_term_months"] == 24

    def test_parse_revenue(self):
        fields = parse_text("Выручка за последний год: 5000000")
        assert fields["revenue_last_year"] == 5000000

    def test_parse_profit(self):
        fields = parse_text("Прибыль за последний год: 350000")
        assert fields["profit_last_year"] == 350000

    def test_parse_employees(self):
        fields = parse_text("Количество сотрудников: 15")
        assert fields["employees_count"] == 15

    def test_parse_business_description(self):
        fields = parse_text("Описание бизнеса: Производство мебели")
        assert fields["business_description"] == "Производство мебели"

    def test_parse_fio_separate_lines(self):
        """ФИО, разбитое на три строки, склеивается в company_name."""
        text = "Фамилия: Иванов\nИмя: Иван\nОтчество: Иванович"
        fields = parse_text(text)
        assert fields["company_name"] == "Иванов Иван Иванович"

    def test_parse_fio_with_whitespace(self):
        text = "  Фамилия :   Иванов  \n Имя :  Иван \n Отчество : Иванович "
        fields = parse_text(text)
        assert fields["company_name"] == "Иванов Иван Иванович"

    def test_parse_money_with_spaces(self):
        """'1 000 000' -> 1000000."""
        fields = parse_text("Запрашиваемая сумма: 1 000 000")
        assert fields["requested_amount"] == 1000000

    def test_parse_money_with_rub(self):
        fields = parse_text("Запрашиваемая сумма: 1 000 000 руб.")
        assert fields["requested_amount"] == 1000000

    def test_parse_unknown_key_ignored(self):
        fields = parse_text("Секретное поле: значение")
        assert fields == {}

    def test_parse_line_without_colon_ignored(self):
        fields = parse_text("Просто строка без двоеточия")
        assert fields == {}


# ============================================================================
# L1: парсинг всех полей вместе
# ============================================================================

class TestParseAllFields:
    def test_full_application_text(self):
        text = (
            "Название компании: ООО Ромашка\n"
            "ИНН: 1234567890\n"
            "Запрашиваемая сумма: 1000000\n"
            "Срок (мес.): 24\n"
            "Выручка за последний год: 5000000\n"
            "Прибыль за последний год: 350000\n"
            "Количество сотрудников: 15\n"
            "Описание бизнеса: Производство и продажа мебели\n"
        )
        fields = parse_text(text)
        assert len(fields) == 8
        assert fields["company_name"] == "ООО Ромашка"
        assert fields["inn"] == "1234567890"
        assert fields["requested_amount"] == 1000000
        assert fields["requested_term_months"] == 24
        assert fields["revenue_last_year"] == 5000000
        assert fields["profit_last_year"] == 350000
        assert fields["employees_count"] == 15
        assert fields["business_description"] == "Производство и продажа мебели"

    def test_full_application_from_pdf(self):
        data = extract_from_pdf(FIXTURES / "case1_success.pdf")
        assert data.company_name == "ООО Ромашка"
        assert data.inn == "1234567890"
        assert data.requested_amount == 1000000
        assert data.missing_fields() == []


# ============================================================================
# L1: роли и слияние
# ============================================================================

class TestRolesAndMerge:
    def test_detect_role_by_name(self):
        from document_agent.extraction import _detect_role
        assert _detect_role("package_anketa.pdf", {}) == ROLE_APPLICATION
        assert _detect_role("package_financial.pdf", {}) == ROLE_FINANCIAL
        assert _detect_role("unknown.pdf", {}) == ROLE_UNKNOWN

    def test_detect_role_by_fields(self):
        from document_agent.extraction import _detect_role
        assert _detect_role("file.pdf", {"inn": "1"}) == ROLE_APPLICATION
        assert _detect_role("file.pdf", {"revenue_last_year": 1}) == ROLE_FINANCIAL

    def test_bundle_roles(self):
        bundle = extract_bundle(
            [FIXTURES / "package_anketa.pdf", FIXTURES / "package_financial.pdf"],
            "Производство мебели",
        )
        assert bundle.roles["package_anketa.pdf"] == ROLE_APPLICATION
        assert bundle.roles["package_financial.pdf"] == ROLE_FINANCIAL
        assert bundle.status == "success"
        assert bundle.conflicts == []

    def test_bundle_priority_application_wins(self):
        """Для requested_amount приоритет — application (анкета)."""
        bundle = extract_bundle([
            FIXTURES / "conflict_financial.pdf",
            FIXTURES / "conflict_anketa.pdf",  # порядок намеренно обратный
        ])
        assert bundle.data.requested_amount == 1000000
        assert bundle.sources["requested_amount"] == "conflict_anketa.pdf"

    def test_bundle_conflict_detected(self):
        bundle = extract_bundle([
            FIXTURES / "conflict_anketa.pdf",
            FIXTURES / "conflict_financial.pdf",
        ])
        assert len(bundle.conflicts) == 1
        c = bundle.conflicts[0]
        assert c["field"] == "requested_amount"
        assert c["chosen"] == 1000000
        assert c["chosen_source"] == "conflict_anketa.pdf"

    def test_bundle_all_broken(self):
        with pytest.raises(ExtractionFailed):
            extract_bundle([FIXTURES / "case4_failed.pdf"])

    def test_form_overrides_pdf_description(self):
        bundle = extract_bundle(
            [FIXTURES / "package_anketa.pdf"],
            business_description="Розничная торговля",
        )
        assert bundle.data.business_description == "Розничная торговля"
        assert bundle.sources["business_description"] == "<form>"


# ============================================================================
# L1: реальные документы (real_docs)
# ============================================================================

class TestRealDocuments:
    """Парсинг документов «как в жизни» (текстовые PDF)."""

    def test_passport_extracts_fio_and_inn(self):
        data = extract_from_pdf(REAL_DOCS / "02_passport.pdf")
        assert data.company_name == "Иванов Иван Иванович"
        assert data.inn == "772747550002"

    def test_passport_missing_other_fields(self):
        """Паспорт сам по себе — не полная заявка."""
        data = extract_from_pdf(REAL_DOCS / "02_passport.pdf")
        assert "requested_amount" in data.missing_fields()
        assert "business_description" in data.missing_fields()

    def test_vypiska_extracts_revenue(self):
        data = extract_from_pdf(REAL_DOCS / "03_vypiska.pdf")
        assert data.revenue_last_year == 11205000

    def test_kud_extracts_profit_and_employees(self):
        data = extract_from_pdf(REAL_DOCS / "04_kud.pdf")
        assert data.revenue_last_year == 11205000
        assert data.profit_last_year == 350000
        assert data.employees_count == 8

    def test_egrul_extracts_inn(self):
        data = extract_from_pdf(REAL_DOCS / "05_egrul.pdf")
        assert data.inn == "772747550002"

    def test_pts_extracts_collateral(self):
        data = extract_from_pdf(REAL_DOCS / "06_pts.pdf")
        assert data.inn == "772747550002"

    def test_full_package_real_docs(self):
        """Все 6 реальных документов -> success, без конфликтов."""
        bundle = extract_bundle([
            REAL_DOCS / "01_anketa.pdf",
            REAL_DOCS / "02_passport.pdf",
            REAL_DOCS / "03_vypiska.pdf",
            REAL_DOCS / "04_kud.pdf",
            REAL_DOCS / "05_egrul.pdf",
            REAL_DOCS / "06_pts.pdf",
        ])
        # Анкета — основной источник company_name (приоритет application)
        assert bundle.status == "success"
        assert bundle.data.company_name == "ООО Ромашка"
        assert bundle.data.inn == "1234567890"   # из анкеты
        assert bundle.data.requested_amount == 1000000
        assert bundle.data.revenue_last_year == 11205000  # из финансов (фикстуры)
        assert len(bundle.roles) == 6


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
        results = to_validation_results(await bus.check_all("validate", data.model_dump()))
        for r in results:
            assert r.status == "passed", f"{r.field}: {r.message}"

    async def test_inn_format_error(self):
        data = ExtractedData(
            company_name="X", inn="123", requested_amount=1000,
            requested_term_months=12, business_description="Y",
        )
        bus = build_validation_bus()
        results = to_validation_results(await bus.check_all("validate", data.model_dump()))
        inn = next(r for r in results if r.field == "inn_format")
        assert inn.status == "error"

    async def test_amount_large_warning(self):
        data = ExtractedData(
            company_name="X", inn="1234567890", requested_amount=10_000_000,
            requested_term_months=12, business_description="Y",
        )
        bus = build_validation_bus()
        results = to_validation_results(await bus.check_all("validate", data.model_dump()))
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
    result = await run_pipeline([FIXTURES / case], "")
    assert result.extraction_status == expected_status
    assert result.requires_manual_review == expected_review


async def test_case1_json_matches_spec():
    result = await run_pipeline([FIXTURES / "case1_success.pdf"], "Мебель")
    data = result.model_dump()
    assert set(data.keys()) == {
        "application_id", "extraction_status", "extracted_data",
        "validation_results", "missing_data",
        "requires_manual_review", "escalation_reason",
    }
    assert set(data["extracted_data"].keys()) == {
        "company_name", "inn", "requested_amount", "requested_term_months",
        "revenue_last_year", "profit_last_year", "business_description",
        "employees_count",
    }


async def test_case2_missing_inn():
    result = await run_pipeline([FIXTURES / "case2_partial.pdf"], "")
    assert result.extraction_status == "partial"
    assert "inn" in result.missing_data


async def test_case3_escalation_reason():
    result = await run_pipeline([FIXTURES / "case3_escalate.pdf"], "")
    assert result.requires_manual_review is True
    assert "5000000" in result.escalation_reason


async def test_case4_failed():
    result = await run_pipeline([FIXTURES / "case4_failed.pdf"], "")
    assert result.extraction_status == "failed"
    assert len(result.missing_data) >= 5


async def test_package_no_conflicts():
    result = await run_pipeline(
        [FIXTURES / "package_anketa.pdf", FIXTURES / "package_financial.pdf"],
        "Производство мебели",
    )
    assert result.extraction_status == "success"
    conflict = next(v for v in result.validation_results if v.field == "field_conflict")
    assert conflict.status == "passed"


async def test_conflict_detected_and_escalated():
    result = await run_pipeline(
        [FIXTURES / "conflict_anketa.pdf", FIXTURES / "conflict_financial.pdf"],
        "",
    )
    conflict = next(v for v in result.validation_results if v.field == "field_conflict")
    assert conflict.status == "warning"
    assert result.requires_manual_review is True


async def test_no_files_fails():
    result = await run_pipeline([], "")
    assert result.extraction_status == "failed"


async def test_audit_verify_after_pipeline():
    await run_pipeline([FIXTURES / "case1_success.pdf"], "Мебель")
    await run_pipeline([FIXTURES / "case2_partial.pdf"], "")
    chain = get_audit_chain()
    assert chain.verify() is True
    assert len(chain) > 0


async def test_failure_cb_opens_after_three_broken():
    for _ in range(3):
        await run_pipeline([FIXTURES / "case4_failed.pdf"], "")
    result = await run_pipeline([FIXTURES / "case4_failed.pdf"], "")
    assert "circuit open" in (result.escalation_reason or "").lower()


# ============================================================================
# L2: полный процесс (вариант B)
# ============================================================================

class TestFullProcess:
    async def test_full_process_real_docs(self):
        """6 реальных документов -> полный процесс с 8 этапами."""
        app = await run_full_process([
            REAL_DOCS / "01_anketa.pdf",
            REAL_DOCS / "02_passport.pdf",
            REAL_DOCS / "03_vypiska.pdf",
            REAL_DOCS / "04_kud.pdf",
            REAL_DOCS / "05_egrul.pdf",
            REAL_DOCS / "06_pts.pdf",
        ], "Розничная торговля")
        assert app.r_level == "R2"
        assert app.workflow_id.startswith("wf_msb_credit_")
        assert len(app.policy_ids) == 5
        assert app.scoring_result["score"] == 685
        assert app.review_result["decision"] == "ESCALATE_TO_HITL"
        assert app.hitl_result["required"] is True
        assert len(app.route) == 8
        assert app.document_agent_result.extraction_status == "success"

    async def test_full_process_audit_verify(self):
        await run_full_process([REAL_DOCS / "01_anketa.pdf"], "Мебель")
        assert get_audit_chain().verify() is True


# ============================================================================
# L3: HTTP
# ============================================================================

class TestWeb:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from document_agent.web.app import app
        from document_agent.web import app as app_module
        app_module.RESULTS.clear()
        app_module.ESCALATIONS.clear()
        app_module.APPLICATIONS.clear()
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
        app_id = location.rsplit("/", 1)[-1]

        j = client.get(f"/result/{app_id}.json")
        assert j.status_code == 200
        data = j.json()
        assert data["extraction_status"] == "success"

    def test_upload_rejects_wrong_extension(self, client):
        r = client.post(
            "/upload",
            files=[("files", ("evil.exe", b"MZ", "application/octet-stream"))],
            data={"business_description": ""},
        )
        assert r.status_code == 400

    def test_upload_rejects_no_files(self, client):
        r = client.post("/upload", data={"business_description": ""})
        assert r.status_code == 400

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
        events = [e.event for e in get_audit_chain().entries]
        assert "escalation.requested" in events

    def test_audit_page_verify(self, client):
        client.get("/")
        r = client.get("/audit")
        assert r.status_code == 200
        assert "verify()" in r.text
