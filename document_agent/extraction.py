"""Извлечение данных из PDF (спринт 1, §7.1) + обработка пакета документов.

Формат фикстур и демо-документов: одна страница, построчно
«Вопрос: ответ», кириллица. Без таблиц, без OCR.

Обработка 1–3 файлов одной заявки (§4.2):
1. _detect_role()          — роль документа: application / financial /
                             description / unknown.
                             По имени файла + набору полей.
2. _parse_one()            — поля одного PDF.
3. _merge_with_priority()  — слияние с приоритетом по §4.1:
                             application > financial > description > unknown.
4. _detect_conflicts()     — если поле встретилось в 2+ файлах с разными
                             значениями -> конфликт (warning в validation).

Что НЕ делаем (спринт 2+):
- OCR для сканов
- таблицы (extract_tables)
- сверка с БКИ/ФНС/ЕГРЮЛ (моки — в mocks.py)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

import pdfplumber

from document_agent.models import ExtractedData


# Строка вида "Вопрос: ответ"
LINE_RE = re.compile(r"^\s*([^:]+?)\s*:\s*(.+?)\s*$")

# Русские подписи -> поля ExtractedData (§6.1)
FIELD_MAP: dict[str, str] = {
    "название компании": "company_name",
    "инн": "inn",
    "запрашиваемая сумма": "requested_amount",
    "срок (мес.)": "requested_term_months",
    "срок": "requested_term_months",
    "выручка за последний год": "revenue_last_year",
    "прибыль за последний год": "profit_last_year",
    "количество сотрудников": "employees_count",
    "описание бизнеса": "business_description",
}

INT_FIELDS = {
    "requested_amount",
    "requested_term_months",
    "revenue_last_year",
    "profit_last_year",
    "employees_count",
}


class ExtractionFailed(Exception):
    """Все файлы оказались нечитаемыми — pipeline ставит status=failed."""


# ==== Роли документов (§4.1) ====

ROLE_APPLICATION = "application"   # Анкета заёмщика
ROLE_FINANCIAL = "financial"       # Финансовая отчётность
ROLE_DESCRIPTION = "description"   # Описание бизнеса
ROLE_UNKNOWN = "unknown"

# Приоритет ролей: меньше число — выше приоритет.
# §4.1: анкета — обязательна и содержит ключевые поля; финотчётность —
# опциональна и содержит revenue/profit.
ROLE_PRIORITY = {
    ROLE_APPLICATION: 0,
    ROLE_FINANCIAL: 1,
    ROLE_DESCRIPTION: 2,
    ROLE_UNKNOWN: 3,
}

# Приоритетный источник для каждого поля (по §4.1).
# Если поле есть в файле с этой ролью — берём оттуда, иначе fallback.
FIELD_PREFERRED_ROLE: dict[str, str] = {
    "company_name": ROLE_APPLICATION,
    "inn": ROLE_APPLICATION,
    "requested_amount": ROLE_APPLICATION,
    "requested_term_months": ROLE_APPLICATION,
    "employees_count": ROLE_APPLICATION,
    "revenue_last_year": ROLE_FINANCIAL,
    "profit_last_year": ROLE_FINANCIAL,
    "business_description": ROLE_DESCRIPTION,
}

# Подсказки по имени файла
ROLE_NAME_HINTS: list[tuple[str, str]] = [
    ("анкета", ROLE_APPLICATION),
    ("anketa", ROLE_APPLICATION),
    ("application", ROLE_APPLICATION),
    ("form", ROLE_APPLICATION),
    ("фин", ROLE_FINANCIAL),
    ("financial", ROLE_FINANCIAL),
    ("отчет", ROLE_FINANCIAL),
    ("отчёт", ROLE_FINANCIAL),
    ("balance", ROLE_FINANCIAL),
    ("описан", ROLE_DESCRIPTION),
    ("description", ROLE_DESCRIPTION),
    ("бизнес", ROLE_DESCRIPTION),
]


@dataclass
class DocumentInfo:
    """Информация об одном файле в пакете."""
    filename: str
    role: str
    fields: dict
    error: str | None = None


@dataclass
class BundleResult:
    """Результат обработки пакета 1–3 файлов одной заявки."""
    data: ExtractedData
    status: str                                # "success" | "partial"
    sources: dict[str, str] = field(default_factory=dict)
    roles: dict[str, str] = field(default_factory=dict)       # filename -> role
    conflicts: list[dict] = field(default_factory=list)       # [{field, values, chosen, chosen_source}]
    documents: list[DocumentInfo] = field(default_factory=list)


# ==== Парсинг ====

def _parse_int(raw: str) -> int | None:
    cleaned = re.sub(r"[^\d-]", "", raw)
    if not cleaned or cleaned == "-":
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _normalize_key(raw: str) -> str:
    return raw.strip().lower().replace("ё", "е")


def parse_text(text: str) -> dict:
    """Разбирает текст PDF в словарь полей ExtractedData."""
    fields: dict = {}
    for line in text.splitlines():
        m = LINE_RE.match(line)
        if not m:
            continue
        key_raw, value_raw = m.group(1), m.group(2)
        field = FIELD_MAP.get(_normalize_key(key_raw))
        if field is None:
            continue
        if field in INT_FIELDS:
            parsed = _parse_int(value_raw)
            if parsed is not None:
                fields[field] = parsed
        else:
            fields[field] = value_raw.strip()
    return fields


def _parse_one(source: str | Path | BinaryIO) -> dict:
    """Открывает PDF и возвращает словарь полей. Бросает при ошибке."""
    with pdfplumber.open(source) as pdf:
        text = "\n".join(
            (page.extract_text() or "") for page in pdf.pages
        )
    return parse_text(text)


# ==== Определение роли ====

def _detect_role(filename: str, fields: dict) -> str:
    """Определяет роль документа: по имени файла, затем по полям."""
    name_lower = filename.lower()

    # 1. По имени файла
    for hint, role in ROLE_NAME_HINTS:
        if hint in name_lower:
            return role

    # 2. По набору полей (fallback)
    keys = set(fields.keys())
    app_markers = {"company_name", "inn", "requested_amount"}
    fin_markers = {"revenue_last_year", "profit_last_year"}

    if keys & app_markers:
        return ROLE_APPLICATION
    if keys & fin_markers:
        return ROLE_FINANCIAL
    if "business_description" in keys:
        return ROLE_DESCRIPTION
    return ROLE_UNKNOWN


# ==== Слияние с приоритетом ====

def _merge_with_priority(
    documents: list[DocumentInfo],
) -> tuple[dict, dict[str, str], list[dict]]:
    """Сливает поля из документов с приоритетом по §4.1.

    Возвращает (merged, sources, conflicts):
    - merged: {field: value}
    - sources: {field: filename}
    - conflicts: [{field, values: {filename: value}, chosen, chosen_source}]

    Правило:
    - для каждого поля ищем файл с приоритетной ролью (FIELD_PREFERRED_ROLE);
    - если такого нет — берём из файла с наибольшим приоритетом роли
      (ROLE_PRIORITY), где поле встретилось;
    - если поле встретилось в 2+ файлах с РАЗНЫМИ значениями — конфликт.
    """
    # Собираем: field -> list[(filename, role, value)]
    occurrences: dict[str, list[tuple[str, str, object]]] = {}

    for doc in documents:
        for field_name, value in doc.fields.items():
            occurrences.setdefault(field_name, []).append(
                (doc.filename, doc.role, value)
            )

    merged: dict = {}
    sources: dict[str, str] = {}
    conflicts: list[dict] = []

    for field_name, occ in occurrences.items():
        preferred_role = FIELD_PREFERRED_ROLE.get(field_name)

        # 1. Приоритетный источник
        chosen = None
        if preferred_role is not None:
            for fname, role, value in occ:
                if role == preferred_role:
                    chosen = (fname, value)
                    break

        # 2. Fallback: файл с наибольшим приоритетом роли
        if chosen is None:
            occ_sorted = sorted(occ, key=lambda x: ROLE_PRIORITY.get(x[1], 99))
            chosen = (occ_sorted[0][0], occ_sorted[0][2])

        merged[field_name] = chosen[1]
        sources[field_name] = chosen[0]

        # 3. Конфликты: разные значения в разных файлах
        distinct = {}
        for fname, _role, value in occ:
            distinct[fname] = value
        unique_values = set(distinct.values())
        if len(unique_values) > 1:
            conflicts.append({
                "field": field_name,
                "values": distinct,
                "chosen": chosen[1],
                "chosen_source": chosen[0],
            })

    return merged, sources, conflicts


# ==== Публичный API ====

def extract_bundle(
    files: list,
    business_description: str = "",
) -> BundleResult:
    """Обрабатывает пакет 1–3 файлов одной заявки.

    Возвращает BundleResult с полями, источниками, ролями и конфликтами.
    Бросает ExtractionFailed, если ни один файл не прочитался.

    files: список UploadFile (с .filename и .file) или путей.
    business_description: значение из формы — перекрывает PDF (роль description).
    """
    documents: list[DocumentInfo] = []
    read_any = False

    for f in files:
        filename = getattr(f, "filename", None) or str(f)
        try:
            source = getattr(f, "file", f)
            fields = _parse_one(source)
            role = _detect_role(filename, fields)
            documents.append(DocumentInfo(filename=filename, role=role, fields=fields))
            read_any = True
        except Exception as exc:
            documents.append(DocumentInfo(
                filename=filename, role=ROLE_UNKNOWN, fields={}, error=str(exc),
            ))

    if not read_any:
        raise ExtractionFailed("Не удалось прочитать ни один файл")

    merged, sources, conflicts = _merge_with_priority(documents)

    # Описание бизнеса из формы — приоритетнее PDF (§4.1: обязательное поле формы)
    if business_description and business_description.strip():
        if "business_description" in merged:
            # Конфликт формы и PDF
            if merged["business_description"] != business_description.strip():
                conflicts.append({
                    "field": "business_description",
                    "values": {
                        sources["business_description"]: merged["business_description"],
                        "<form>": business_description.strip(),
                    },
                    "chosen": business_description.strip(),
                    "chosen_source": "<form>",
                })
        merged["business_description"] = business_description.strip()
        sources["business_description"] = "<form>"

    data = ExtractedData(**merged)
    status = "success" if not data.missing_fields() else "partial"

    return BundleResult(
        data=data,
        status=status,
        sources=sources,
        roles={doc.filename: doc.role for doc in documents},
        conflicts=conflicts,
        documents=documents,
    )


# ==== Обратная совместимость ====

def extract_from_files_with_sources(
    files: list,
    business_description: str,
) -> tuple[ExtractedData, str, dict[str, str]]:
    """Старый API: (data, status, sources). Без roles и conflicts."""
    bundle = extract_bundle(files, business_description)
    return bundle.data, bundle.status, bundle.sources


def extract_from_files(
    files: list,
    business_description: str,
) -> tuple[ExtractedData, str]:
    """Самый старый API: (data, status)."""
    data, status, _ = extract_from_files_with_sources(files, business_description)
    return data, status


def extract_from_pdf(source: str | Path | BinaryIO) -> ExtractedData:
    """Один PDF -> ExtractedData. Для тестов и отладки."""
    return ExtractedData(**_parse_one(source))
