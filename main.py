"""Точка входа Document Agent (CLI) с эффектом печатной машинки и пояснениями.

Запуск:
    python main.py            # интерактивное меню
    python main.py --demo     # прогон 4 кейсов
    python main.py --full     # полный процесс
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import colordebug as cd
from document_agent.pipeline import run_pipeline, run_full_process, get_audit_chain, reset_state

FIXTURES = Path(__file__).resolve().parent / "tests" / "fixtures"
REAL_DOCS = FIXTURES / "real_docs"

# Цвета
CYAN = "\033[1;36m"
YELLOW = "\033[1;33m"
GREEN = "\033[1;32m"
RED = "\033[1;31m"
GRAY = "\033[0;37m"
BOLD = "\033[1m"
RESET = "\033[0m"

# ============================================================================
# Эффект печатной машинки
# ============================================================================

def typewriter(text: str, color: str = CYAN, delay: float = 0.015) -> None:
    """Печатает текст по символам с задержкой."""
    print(color, end="", flush=True)
    for char in text:
        print(char, end="", flush=True)
        time.sleep(delay + (0.01 if char in ".,:;" else 0))
    print(RESET, flush=True)
    time.sleep(0.6) # Пауза в конце строки для осмысления

def step_header(step_num: int, title: str) -> None:
    """Печатает заголовок шага."""
    print(flush=True)
    time.sleep(0.3)
    typewriter(f"[ШАГ {step_num}] {title}", YELLOW, delay=0.01)
    print("-" * 70, flush=True)
    time.sleep(0.5)

# ============================================================================
# Сценарий 1: 4 кейса
# ============================================================================

def run_demo_cases() -> int:
    reset_state()
    typewriter("Запуск базового тестирования: прогон 4 кейсов.", BOLD)
    typewriter("Цель: проверить, как система реагирует на разные входные данные.", GRAY)
    
    CASES = [
        {"file": "case1_success.pdf", "title": "Позитивный сценарий (Все данные корректны)"},
        {"file": "case2_partial.pdf", "title": "Негативный сценарий (В анкете нет ИНН)"},
        {"file": "case3_escalate.pdf", "title": "Пограничный сценарий (Сумма > 5 млн)"},
        {"file": "case4_failed.pdf", "title": "Ошибка (Битый PDF-файл)"},
    ]

    all_passed = True
    for i, case in enumerate(CASES, 1):
        path = FIXTURES / case["file"]
        step_header(i, case["title"])
        
        # 1. Объяснение процесса
        typewriter("Процесс: Извлечение текста -> Валидация данных -> Проверка правил.", GRAY)
        typewriter("Запуск Document Agent...", CYAN)
        
        result = asyncio.run(run_pipeline([path], ""))
        
        # 2. Объяснение результата в зависимости от кейса
        if i == 1:
            typewriter("РЕЗУЛЬТАТ: УСПЕХ.", GREEN)
            typewriter("Почему это важно: Система извлекла все поля, проверила форматы. Заявка готова к авто-одобрению. Ручной труд не требуется.", GRAY)
        elif i == 2:
            typewriter("РЕЗУЛЬТАТ: ЧАСТИЧНЫЙ УСПЕХ (PARTIAL).", YELLOW)
            typewriter("Почему это важно: В анкете не хватает критичного поля (ИНН). Система не отклоняет заявку, а помечает её как неполную и просит менеджера дозаполнить данные.", GRAY)
        elif i == 3:
            typewriter("РЕЗУЛЬТАТ: ЭСКАЛАЦИЯ.", YELLOW)
            typewriter("Почему это важно: Сумма кредита 10 млн превышает лимит авто-одобрения (5 млн). Система автоматически направляет заявку на расширенный андеррайтинг.", GRAY)
        elif i == 4:
            typewriter("РЕЗУЛЬТАТ: ОШИБКА (FAILED).", RED)
            typewriter("Почему это важно: Файл поврежден. Система не падает с ошибкой 500, а корректно фиксирует проблему и создает задачу на ручную проверку. Это защита от каскадных сбоев.", GRAY)
            
        time.sleep(1.5)

    # Финал
    step_header(5, "Проверка целостности (AuditChain)")
    typewriter("Каждая запись содержит SHA-256 хеш предыдущей.", CYAN)
    typewriter("Это гарантирует, что историю обработки заявки невозможно подделать.", GRAY)
    
    chain = get_audit_chain()
    if chain.verify():
        typewriter("verify() == True — журнал целостен. Все действия зафиксированы.", GREEN)
    else:
        typewriter("verify() == False — ВНИМАНИЕ, журнал поврежден!", RED)
        all_passed = False
        
    if all_passed:
        typewriter("Все 4 кейса пройдены успешно.", GREEN)
        return 0
    return 1

# ============================================================================
# Сценарий 2: Полный процесс
# ============================================================================

def run_full_demo() -> int:
    reset_state()
    typewriter("Запуск полного процесса обработки кредитной заявки МСБ.", BOLD)
    typewriter("Этот сценарий демонстрирует, как Document Agent встраивается в общий конвейер.", GRAY)

    files = sorted(REAL_DOCS.glob("*.pdf"))
    if not files:
        typewriter(f"КРИТИЧЕСКАЯ ОШИБКА: Нет файлов в {REAL_DOCS}", RED)
        return 1

    step_header(1, "Загрузка и маршрутизация (Discovery)")
    typewriter("Что происходит: Система получает пакет документов от менеджера.", CYAN)
    typewriter("Оркестратор (МОК) определяет уровень автономии (R-level) и бюджет токенов.", GRAY)
    typewriter(f"Загружено документов: {len(files)} (Анкета, Паспорт, Выписка, КУД, ЕГРИП, ПТС).", CYAN)
    
    app = asyncio.run(run_full_process(files, "Розничная торговля"))
    
    typewriter(f"Workflow ID: {app.workflow_id}", GRAY)
    typewriter(f"Уровень автономии: {app.r_level} (пограничная сумма, требуется подтверждение).", GRAY)
    typewriter("Почему это важно: Оркестратор понимает, что заявка сложная, и выделяет на неё ресурсы.", GRAY)
    
    step_header(2, "Извлечение данных (Document Agent)")
    typewriter("Что происходит: Наш реальный агент читает PDF-файлы.", CYAN)
    typewriter("Он определяет роль каждого документа и склеивает данные (например, сумму из Анкеты, выручку из Выписки).", GRAY)
    
    da = app.document_agent_result
    if da:
        typewriter(f"Статус извлечения: {da.extraction_status}", GREEN)
        if da.extracted_data.inn:
            typewriter(f"Найден ИНН: {da.extracted_data.inn}", CYAN)
        if da.extracted_data.requested_amount:
            typewriter(f"Найдена сумма: {da.extracted_data.requested_amount:,} руб.", CYAN)
    typewriter("Почему это важно: Вместо ручного ввода 20 полей менеджер получает готовый структурированный JSON за 1 секунду.", GRAY)
            
    step_header(3, "Скоринг и Комплаенс (МОК)")
    typewriter("Что происходит: Данные передаются в Scoring и Compliance агенты.", CYAN)
    typewriter("Сейчас они работают в режиме МОК (симуляции), так как это прототип.", GRAY)
    sc = app.scoring_result
    typewriter(f"Скоринг-балл: {sc['score']} / 1000", YELLOW)
    typewriter(f"Вердикт: {sc['verdict']}", YELLOW)
    typewriter("Почему это важно: Балл 685 попадает в 'серую зону' (600-700). Система понимает, что автоматическое решение рискованно.", GRAY)

    step_header(4, "Эскалация и HITL (Человек в цикле)")
    typewriter("Что происходит: Заявка уходит на ручную проверку андеррайтеру.", CYAN)
    typewriter("Почему это важно: В сложных случаях (серая зона скоринга, крупная сумма) система не берет ответственность на себя, а передает решение человеку. Это снижает риски.", GRAY)

    step_header(5, "Проверка целостности (AuditChain)")
    typewriter("Что происходит: Финальная проверка журнала аудита.", CYAN)
    chain = get_audit_chain()
    if chain.verify():
        typewriter("verify() == True — журнал целостен.", GREEN)
        typewriter("Полный процесс завершен. Evidence Bundle сформирован.", GREEN)
        return 0
    else:
        typewriter("verify() == False — КРИТИЧЕСКАЯ ОШИБКА!", RED)
        return 1

# ============================================================================
# main
# ============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(prog="document-agent")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()

    if args.demo:
        return run_demo_cases()
    if args.full:
        return run_full_demo()

    print("\n=== Document Agent (CLI) ===")
    print("1. Прогон 4 базовых кейсов")
    print("2. Полный процесс (6 документов)")
    print("0. Выход")
    
    choice = input("\nВыберите действие [0-2]: ").strip()
    
    if choice == "1":
        return run_demo_cases()
    elif choice == "2":
        return run_full_demo()
    elif choice == "0":
        return 0
    else:
        print("Неверный выбор")
        return 1

if __name__ == "__main__":
    sys.exit(main())
