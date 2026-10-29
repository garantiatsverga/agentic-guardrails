"""Модель кредитной заявки МСБ (по документу «Процесс подачи заявки»).

Описывает заявку на **всём** пути: от подачи в IDP Portal до финального
решения. В спринте 1 **реально** заполняется только document_agent_result;
оркестратор, scoring, compliance, review, hitl — МОКИ (см. docs/simplifications.md).

Разделы соответствуют документу коллеги (Ганжа И):
1. Служебные метаданные
2. Сведения о заёмщике
3. Параметры кредита
4. Финансовые показатели (декларация)
5. Кредитная история (декларация)
6. Обеспечение (декларация)
7. Согласия
8. Документы (чек-лист)
9. Скоринг (результат Scoring Agent — МОК)
10. Маршрут (результат Оркестратора — МОК)
11. Evidence Bundle
12. Штамп
"""
from __future__ import annotations

from dataclasses import dataclass, field

from document_agent.models import ApplicationResult


@dataclass
class CreditApplication:
    """Кредитная заявка МСБ. Одна заявка = один workflow."""

    # ==== Раздел 1: служебные (IDP Portal + Оркестратор) ====
    application_id: str = ""
    idempotency_key: str = ""
    channel: str = "ВТБ-Бизнес Онлайн (интернет-банк)"
    submitted_at: str = "21.02.2026, 10:42 МСК"
    branch: str = "ДО «Южно-Сахалинск», код 2026-693-01"
    manager: str = "Код сотр. ВТБ-77421, Петров А.С."
    product: str = "«ВТБ-Экспресс» — экспресс-кредит для МСБ"

    # Оркестратор (заполняется в Discovery-фазе — МОК)
    task_id: str = ""
    workflow_id: str = ""
    session_id: str = ""
    spec_pointer: str = "sdd-registry://msb-credit-processing/v2.3"
    r_level: str = ""
    token_budget: dict = field(default_factory=dict)
    policy_ids: list[str] = field(default_factory=list)
    sandbox_profile: str = "PROD"

    # ==== Раздел 2: сведения о заёмщике ====
    legal_form: str = "Индивидуальный предприниматель"
    full_name: str = "Иванов Иван Иванович"
    inn: str = "650100710760"
    ogrnip: str = "304650431500022"
    registration_date: str = "17.01.2015"
    address_registered: str = "693006, Сахалинская обл., г. Южно-Сахалинск, ул. Ленина, д. 1, кв. 1"
    address_business: str = "693006, Сахалинская обл., г. Южно-Сахалинск, ул. Ленина, д. 15"
    okved_main: str = "47.11.3"
    tax_regime: str = "УСН (6%)"
    phone: str = "+7 (914) 645-00-00"
    email: str = "email@mail.ru"
    contact_person: str = "Петрова Светлана Алексеевна, +7 (914) 753-01-00"

    # ==== Раздел 3: параметры кредита ====
    requested_amount: int = 5_000_000
    term_months: int = 36
    purpose: str = "Пополнение оборотных средств (закуп товаров)"
    purpose_detail: str = "Продовольственные товары, детское питание, игрушки, кондитерские изделия"
    disbursement: str = "На расчётный счёт заёмщика"

    # Предварительные (после скоринга — МОК)
    interest_rate: float | None = None       # 23.9
    monthly_payment: int | None = None       # 195_500

    # ==== Раздел 4: финансовые показатели (декларация) ====
    revenue_last_year: int = 11_205_000
    revenue_ytd: int = 1_890_000
    taxes_paid_last_year: int = 625_000
    employees_count: int = 8
    avg_salary: int = 41_000
    avg_monthly_turnover: int = 940_000

    # ==== Раздел 5: кредитная история (декларация) ====
    credit_history: list[dict] = field(default_factory=lambda: [
        {"creditor": "ПАО Сбербанк", "amount": 15_000,
         "date": "28.03.2022", "term": "27.03.2025", "balance": 12_000},
    ])

    # ==== Раздел 6: обеспечение ====
    collateral_type: str = "Залог транспортного средства"
    collateral_property: str = "Toyota Land Cruiser Prado, 2010 г.в."
    collateral_owner: str = "Заёмщик"
    collateral_value: int = 3_000_000

    # ==== Раздел 7: согласия ====
    consents: dict[str, bool] = field(default_factory=lambda: {
        "Достоверность сведений": True,
        "Не является кредитной организацией / игорный бизнес / нерезидент": True,
        "Нет процедур банкротства": True,
        "Согласие на запрос кредитной истории в БКИ": True,
        "Согласие на запрос данных из ФНС, ПФР, ЕГРЮЛ": True,
        "Согласие на автоматизированную обработку алгоритмами ИИ": True,
        "Согласие на обработку ПДН по 152-ФЗ": True,
        "Согласие по 115-ФЗ (ПОД/ФТ)": True,
        "Залог застрахован": True,
        "Согласие на уведомления (СМС, e-mail, мессенджеры)": True,
    })

    # ==== Раздел 8: документы (чек-лист) ====
    documents: list[dict] = field(default_factory=lambda: [
        {"num": 1, "name": "Анкета-заявление ИП", "file": "anketa.pdf", "status": "uploaded"},
        {"num": 2, "name": "Паспорт ИП", "file": "passport.pdf", "status": "uploaded"},
        {"num": 3, "name": "Свидетельство ОГРНИП", "file": "ogrnip.pdf", "status": "uploaded"},
        {"num": 4, "name": "Книга учёта доходов (УСН)", "file": "kud_2025.pdf", "status": "uploaded"},
        {"num": 5, "name": "Выписка по расчётному счёту (6 мес.)", "file": "vypiska.pdf", "status": "uploaded"},
        {"num": 6, "name": "ПТС на залог (авто)", "file": "pts.pdf", "status": "mock"},
        {"num": 7, "name": "Договор страхования залога", "file": "strah.pdf", "status": "mock"},
    ])

    # ==== Раздел 9: скоринг (результат Scoring Agent — МОК) ====
    scoring_result: dict = field(default_factory=dict)

    # ==== Раздел 10: маршрут (результат Оркестратора — МОК) ====
    route: list[dict] = field(default_factory=list)

    # ==== Раздел 11: Evidence Bundle (реально — AuditChain) ====
    evidence_bundle_ref: str = ""
    audit_pointer: str = ""

    # ==== Раздел 12: штамп ====
    final_status: str = "ПРИНЯТА_К_ОБРАБОТКЕ"
    expected_decision_at: str = "21.02.2026 до 14:42 МСК"

    # ==== РЕАЛЬНЫЙ результат Document Agent (спринт 1) ====
    document_agent_result: ApplicationResult | None = None

    # ==== МОКИ остальных агентов ====
    compliance_result: dict = field(default_factory=dict)
    review_result: dict = field(default_factory=dict)
    hitl_result: dict = field(default_factory=dict)

    # ==== Флаги: что реально, что мок ====
    # Для UI: пометка [МОК] на разделах
    mock_sections: list[str] = field(default_factory=lambda: [
        "orchestrator", "scoring", "compliance", "review", "hitl",
        "credit_history", "collateral", "consents", "documents_pts",
    ])
    real_sections: list[str] = field(default_factory=lambda: [
        "document_agent", "audit", "validation",
    ])
