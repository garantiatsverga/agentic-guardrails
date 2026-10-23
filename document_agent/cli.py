"""CLI-прогон 4 кейсов §12 — для записи видео-демо.

Запуск:
    python main.py --cli
    # или:
    python -m document_agent.cli

Цветной вывод — через colordebug (импортируется как cd, чтобы
не засорять пространство имён общими именами info/error/warning
и избежать коллизий с logging или другими модулями).

В конце — финальный cd-вывод chain.verify() == True:
tamper-evident аудит по всем 4 кейсам. Это финальный кадр видео.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import colordebug as cd

from document_agent.pipeline import run_pipeline, get_audit_chain, reset_state


FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

CASES = [
    {
        "id": "case1",
        "file": "case1_success.pdf",
        "business_description": "Производство и продажа мебели",
        "expect_status": "success",
        "expect_review": False,
        "title": "Кейс 1: позитивный — все данные корректны",
    },
    {
        "id": "case2",
        "file": "case2_partial.pdf",
        "business_description": "",
        "expect_status": "partial",
        "expect_review": True,
        "title": "Кейс 2: негативный — отсутствует ИНН",
    },
    {
        "id": "case3",
        "file": "case3_escalate.pdf",
        "business_description": "Производство мебели",
        "expect_status": "success",
        "expect_review": True,
        "title": "Кейс 3: пограничный — сумма > 5 млн",
    },
    {
        "id": "case4",
        "file": "case4_failed.pdf",
        "business_description": "",
        "expect_status": "failed",
        "expect_review": True,
        "title": "Кейс 4: ошибка — файл не поддаётся парсингу",
    },
]


def _print_result(case: dict, result) -> bool:
    """Печатает результат одного кейса. Возвращает True, если совпало с ожиданием."""
    ok_status = result.extraction_status == case["expect_status"]
    ok_review = result.requires_manual_review == case["expect_review"]
    passed = ok_status and ok_review

    if passed:
        cd.success(
            f"[{case['id']}] {result.extraction_status} | "
            f"review={result.requires_manual_review}"
        )
    else:
        cd.error(
            f"[{case['id']}] ожидали {case['expect_status']}/"
            f"{case['expect_review']}, получили "
            f"{result.extraction_status}/{result.requires_manual_review}"
        )

    if result.escalation_reason:
        cd.info(f"  причина: {result.escalation_reason}")
    if result.missing_data:
        cd.warning(f"  missing: {result.missing_data}")
    return passed


async def run_demo_cases() -> int:
    """Прогон 4 кейсов. Возвращает 0, если все совпали с ожиданием, 1 иначе."""
    reset_state()

    cd.info("=" * 70)
    cd.info("Document Agent — спринт 1. Прогон 4 кейсов из §12")
    cd.info("=" * 70)

    all_passed = True

    for case in CASES:
        path = FIXTURES / case["file"]
        if not path.exists():
            cd.critical(f"Фикстура не найдена: {path}")
            return 1

        cd.info("")
        cd.info(f"▶ {case['title']}")
        cd.info(f"  файл: {case['file']}")

        with cd.timer(f"pipeline {case['id']}"):
            result = await run_pipeline(
                files=[str(path)],
                business_description=case["business_description"],
            )

        if not _print_result(case, result):
            all_passed = False

    # ---- Финальный кадр: аудит ----
    cd.info("")
    cd.info("=" * 70)
    chain = get_audit_chain()
    audit_ok = chain.verify()

    cd.log_value("audit_entries", len(chain), exp=True)
    cd.log_value("audit_verify", audit_ok, exp=True)

    if audit_ok:
        cd.success("AuditChain.verify() == True — журнал целостен")
    else:
        cd.critical("AuditChain.verify() == False — журнал повреждён")

    cd.info("=" * 70)
    if all_passed and audit_ok:
        cd.success("Все 4 кейса прошли. Прототип работает.")
        return 0
    else:
        cd.error("Есть расхождения. См. вывод выше.")
        return 1


def main() -> int:
    try:
        return asyncio.run(run_demo_cases())
    except KeyboardInterrupt:
        cd.warning("Прервано пользователем")
        return 130
    except Exception as exc:
        cd.critical(f"Непредвиденная ошибка: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
