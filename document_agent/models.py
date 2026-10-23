"""Pydantic-модели Document Agent (спринт 1).

Соответствие §6.1 спецификации:
- ApplicationResult  -> корневой объект результата
- ExtractedData      -> extracted_data (все поля извлекаемых данных)
- ValidationResult   -> элемент validation_results

Поля JSON — латиницей (как в §6.1).
Подписи в PDF — русские (см. extraction.FIELD_MAP).
"""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, field_validator


ExtractionStatus = Literal["success", "partial", "failed"]
ValidationStatus = Literal["passed", "warning", "error"]


class ExtractedData(BaseModel):
    """Извлечённые из документов данные (§6.1, extracted_data).

    Все поля опциональны: при partial/failed часть может отсутствовать.
    Валидация значений (ИНН 10 цифр, сумма > 0) — в validation.py,
    не здесь: модель только хранит то, что извлекли.
    """
    company_name: str | None = None
    inn: str | None = None
    requested_amount: int | None = None
    requested_term_months: int | None = None
    revenue_last_year: int | None = None
    profit_last_year: int | None = None
    business_description: str | None = None
    employees_count: int | None = None

    # Поля, обязательные по §4.1 (Анкета + Описание бизнеса)
    REQUIRED_FIELDS: ClassVar[tuple[str, ...]] = (
        "company_name", "inn", "requested_amount",
        "requested_term_months", "business_description",
    )

    def missing_fields(self) -> list[str]:
        """Список обязательных полей, которых нет или они пустые."""
        missing = []
        for name in self.REQUIRED_FIELDS:
            val = getattr(self, name)
            if val is None or (isinstance(val, str) and not val.strip()):
                missing.append(name)
        return missing


# ClassVar импортируем отдельно, чтобы не путать с pydantic-полем
from typing import ClassVar  # noqa: E402


class ValidationResult(BaseModel):
    """Один результат валидации (§6.1, validation_results[])."""
    field: str
    status: ValidationStatus
    message: str = ""


class ApplicationResult(BaseModel):
    """Корневой результат обработки заявки (§6.1).

    Это ровно тот JSON, который отдаётся в скоринг:
        result.model_dump()
    """
    application_id: str
    extraction_status: ExtractionStatus
    extracted_data: ExtractedData
    validation_results: list[ValidationResult] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    requires_manual_review: bool = False
    escalation_reason: str | None = None

    @field_validator("escalation_reason")
    @classmethod
    def _reason_consistent_with_flag(cls, v: str | None, info) -> str | None:
        """Если requires_manual_review=False, reason должен быть None."""
        review = info.data.get("requires_manual_review", False)
        if not review and v is not None:
            # Не падаем — просто чистим, чтобы JSON был консистентен
            return None
        return v

    # ---- Удобные конструкторы для pipeline.py ----

    @classmethod
    def failed(cls, application_id: str, reason: str) -> "ApplicationResult":
        """Заявка, которую не удалось обработать (§8.1, extraction_status=failed)."""
        return cls(
            application_id=application_id,
            extraction_status="failed",
            extracted_data=ExtractedData(),
            validation_results=[],
            missing_data=list(ExtractedData.REQUIRED_FIELDS),
            requires_manual_review=True,
            escalation_reason=reason,
        )

    @classmethod
    def partial(
        cls,
        application_id: str,
        data: ExtractedData,
        validation: list[ValidationResult],
    ) -> "ApplicationResult":
        """Извлечено частично: есть missing_data (§8.1)."""
        return cls(
            application_id=application_id,
            extraction_status="partial",
            extracted_data=data,
            validation_results=validation,
            missing_data=data.missing_fields(),
            requires_manual_review=True,
            escalation_reason=(
                f"Отсутствуют обязательные поля: {', '.join(data.missing_fields())}"
                if data.missing_fields() else None
            ),
        )
