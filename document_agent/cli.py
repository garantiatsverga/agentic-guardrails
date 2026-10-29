"""CLI-демо Document Agent.

Выводит цветной результат (через colordebug), показывает извлечение,
валидацию, эскалацию и финальный AuditChain.verify().
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import colordebug as cd

from document_agent.pipeline import run_pipeline, run_full_process, get_audit_chain, reset_state


FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
REAL_DOCS = FIXTURES / "real_docs"

# Базовые кейсы
CASES = [
    {"id": "case1", "file": "case1_success.pdf", "title": "Позитивный (success)"},
    {"id": "case2", "file": "case2_partial.pdf", "title": "Негативный (partial, нет ИНН)"},
    {"id": "case3", "file": "case3_escalate.pdf", "title": "Пограничный (сумма > 5 млн)"},
    {"id": "case4", "file": "case4_failed.pdf", "title": "Ошибка (битый PDF)"},
]


def run_demo_cases() -> int:
    """Прогон 4 кейсов (§12)."""
    reset_state()
    cd.info("=" * 70)
    cd.info("Document Agent — прогон 4 кейсов")
    cd.info("=" * 70)

    all_passed = True
    for case in CASES:
        path = FIXTURES / case["file"]
        if not path.exists():
            cd.critical(f"Не найдена фикстура: {path}")
            return 1
            
        cd.info(f"\n▶ {case['title']}")
        result = asyncio.run(run_pipeline([path], ""))
        
        ok_status = result.extraction_status in ("success", "partial", "failed")
        if result.extraction_status == "failed" and case["id"] != "case4":
            ok_status = False
            
        if ok_status:
            cd.success(f"  Статус: {result.extraction_status} | Review: {result.requires_manual_review}")
        else:
            cd.error(f"  Статус: {result.extraction_status} (ожидалось другое)")
            all_passed = False
            
        if result.escalation_reason:
            cd.info(f"  Причина: {result.escalation_reason}")
        if result.missing_data:
            cd.warning(f"  Отсутствуют: {result.missing_data}")

    # Финал
    cd.info("\n" + "=" * 70)
    chain = get_audit_chain()
    if chain.verify():
        cd.success("AuditChain.verify() == True — журнал целостен")
    else:
        cd.critical("AuditChain.verify() == False — журнал повреждён!")
        all_passed = False
        
    if all_passed:
        cd.success("Все 4 кейса прошли.")
        return 0
    return 1


def run_full_demo() -> int:
    """Прогон полного процесса на реальных документах."""
    reset_state()
    cd.info("=" * 70)
    cd.info("Document Agent — Полный процесс (8 этапов)")
    cd.info("=" * 70)

    # Собираем все PDF из real_docs
    files = sorted(REAL_DOCS.glob("*.pdf"))
    if not files:
        cd.critical(f"Не найдены документы в {REAL_DOCS}")
        return 1

    cd.info(f"Загружено документов: {len(files)}")
    for f in files:
        cd.debug(f"  - {f.name}")

    # Запускаем
    app = asyncio.run(run_full_process(files, "Розничная торговля"))

    # Выводим результаты
    cd.info("\n--- Результат обработки ---")
    
    # 1. Оркестратор
    cd.info(f"Workflow ID: {app.workflow_id}")
    cd.info(f"R-level: {app.r_level}")
    cd.info(f"Политики: {', '.join(app.policy_ids)}")
    
    # 2. Document Agent
    da = app.document_agent_result
    if da:
        if da.extraction_status == "success":
            cd.success(f"Document Agent: {da.extraction_status}")
        else:
            cd.warning(f"Document Agent: {da.extraction_status} (review={da.requires_manual_review})")
        
        if da.extracted_data.company_name:
            cd.info(f"  Компания: {da.extracted_data.company_name}")
        if da.extracted_data.inn:
            cd.info(f"  ИНН: {da.extracted_data.inn}")
        if da.extracted_data.requested_amount:
            cd.info(f"  Сумма: {da.extracted_data.requested_amount:,} ₽")
            
    # 3. Скоринг (МОК)
    sc = app.scoring_result
    cd.info(f"\nScoring Agent [МОК]: {sc['score']} / 1000")
    cd.info(f"  Вердикт: {sc['verdict']}")
    cd.info(f"  Риск-категория: {sc['risk_category']}")
    
    # 4. Маршрут
    cd.info("\nМаршрут обработки [МОК]:")
    for step in app.route:
        status = step["status"]
        if "выполнено" in status:
            cd.success(f"  {step['num']}. {step['agent']} — {status}")
        elif "ожидает" in status:
            cd.warning(f"  {step['num']}. {step['agent']} — {status}")
        else:
            cd.info(f"  {step['num']}. {step['agent']} — {status}")

    # Финал
    cd.info("\n" + "=" * 70)
    chain = get_audit_chain()
    if chain.verify():
        cd.success("AuditChain.verify() == True — журнал целостен")
        cd.success("Полный процесс завершён.")
        return 0
    else:
        cd.critical("AuditChain.verify() == False!")
        return 1
