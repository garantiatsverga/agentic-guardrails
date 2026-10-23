"""Моки внешних систем (спринт 1, §7.2)."""
from __future__ import annotations


def mock_bki(inn: str | None) -> dict:
    return {"source": "mock", "inn": inn, "credit_history": "good",
            "active_loans": 2, "overdue_last_12m": 0}


def mock_fns(inn: str | None) -> dict:
    return {"source": "mock", "inn": inn, "status": "active",
            "registered_at": "2018-03-15", "tax_regime": "УСН"}


def mock_egrul(inn: str | None) -> dict:
    return {"source": "mock", "inn": inn, "legal_name": "ООО Ромашка",
            "director": "Иванов И.И.", "okved": "31.01"}


def collect_external(inn: str | None) -> dict:
    return {"bki": mock_bki(inn), "fns": mock_fns(inn), "egrul": mock_egrul(inn)}
