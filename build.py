#!/usr/bin/env python3
"""build.py — кроссплатформенная сборка Document Agent + agentic_guardrails.

Заменяет build.sh. Работает на Linux, macOS, Windows.

Запуск:
    python build.py              # полная сборка + тесты + покрытие + demo
    python build.py --no-test    # только сборка
    python build.py --no-demo    # без smoke-теста и CLI
    python build.py --no-cov     # без покрытия
    python build.py --clean      # снести .venv и собрать с нуля
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
VENV_DIR = PROJECT_ROOT / ".venv"
PYTHON_MIN = (3, 10)

# Зависимости для разработки (ставятся в venv)
DEV_DEPS = [
    "pytest",
    "pytest-asyncio",
    "pytest-cov",
    "hypothesis",
]

# Файлы тестов (явно, без автопоиска)
TEST_FILES = [
    "tests/test_guardrails.py",
    "tests/test_retry_policy.py",
    "tests/test_rate_limiter.py",
    "tests/test_parallel_runner.py",
    "tests/test_orchestrator.py",
    "tests/test_document_agent.py",
]
PROPERTY_TEST = "tests/test_properties.py"


# ==== Цвета (работают на Linux/macOS, на Windows — отключаются) ====
if platform.system() == "Windows":
    RED = GREEN = YELLOW = BLUE = BOLD = NC = ""
else:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[0;33m"
    BLUE = "\033[0;34m"
    BOLD = "\033[1m"
    NC = "\033[0m"


def log(msg: str) -> None:
    print(f"{BLUE}▸{NC} {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"{GREEN}✓{NC} {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"{YELLOW}⚠{NC} {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"{RED}✗{NC} {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def header(msg: str) -> None:
    print(f"\n{BOLD}=== {msg} ==={NC}", flush=True)


def run(cmd: list[str], cwd: Path = PROJECT_ROOT, check: bool = True) -> subprocess.CompletedProcess:
    """Запускает команду и возвращает результат."""
    try:
        return subprocess.run(
            cmd, cwd=str(cwd), check=check,
            text=True, capture_output=False,
        )
    except subprocess.CalledProcessError as e:
        fail(f"Команда упала: {' '.join(cmd)} (код {e.returncode})")
        raise


# ==== Шаги ====

def check_python() -> None:
    header("Проверка окружения")
    if sys.version_info < PYTHON_MIN:
        fail(f"Требуется Python >= {PYTHON_MIN[0]}.{PYTHON_MIN[1]}, "
             f"найден {sys.version_info.major}.{sys.version_info.minor}")
    ok(f"python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    ok(f"платформа: {platform.system()} ({platform.machine()})")


def venv_python() -> Path:
    """Путь к python внутри venv (кроссплатформенно)."""
    if platform.system() == "Windows":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def ensure_venv() -> Path:
    header("Виртуальное окружение")
    py = venv_python()
    if not py.exists():
        log(f"Создаю {VENV_DIR}...")
        run([sys.executable, "-m", "venv", str(VENV_DIR)])
        ok("venv создан")
    else:
        ok("venv уже существует")
    ok(f"python: {py}")
    return py


def install_deps(py: Path) -> None:
    header("Установка зависимостей")
    run([str(py), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    run([str(py), "-m", "pip", "install", "--quiet", *DEV_DEPS])
    run([str(py), "-m", "pip", "install", "--quiet", "-e", str(PROJECT_ROOT)])
    for dep in DEV_DEPS:
        r = subprocess.run(
            [str(py), "-m", "pip", "show", dep],
            capture_output=True, text=True,
        )
        ver = "?"
        for line in r.stdout.splitlines():
            if line.startswith("Version:"):
                ver = line.split(":", 1)[1].strip()
        ok(f"{dep} {ver}")


def check_imports(py: Path) -> None:
    header("Проверка импортов")
    for mod in ("agentic_guardrails", "document_agent", "main"):
        r = subprocess.run(
            [str(py), "-c", f"import {mod}; print('{mod} OK')"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            fail(f"{mod} не импортируется:\n{r.stderr}")
        ok(f"{mod} импортируется")


def run_tests(py: Path, no_test: bool) -> list[str]:
    if no_test:
        return []
    header("Python-тесты")
    existing = []
    for f in TEST_FILES:
        if (PROJECT_ROOT / f).exists():
            existing.append(f)
        else:
            warn(f"Пропускаю отсутствующий {f}")
    if not existing:
        warn("Не найдено тестовых файлов — пропускаю")
        return []

    run([str(py), "-m", "pytest", "-q", *existing])
    ok("Основные тесты прошли")

    if (PROJECT_ROOT / PROPERTY_TEST).exists():
        run([str(py), "-m", "pytest", "-q", PROPERTY_TEST])
        ok("Property-based тесты прошли")
    return existing


def run_coverage(py: Path, tests: list[str], no_cov: bool) -> None:
    if no_cov or not tests:
        return
    header("Покрытие")
    r = subprocess.run(
        [str(py), "-m", "pytest", "--quiet",
         "--cov=agentic_guardrails", "--cov=document_agent",
         "--cov-report=term-missing", *tests],
        capture_output=True, text=True,
    )
    # Показываем последние 30 строк
    lines = r.stdout.splitlines()
    for line in lines[-30:]:
        print(line)


def run_demo(py: Path, no_demo: bool) -> None:
    if no_demo:
        return
    header("Smoke test (examples/demo.py)")
    demo = PROJECT_ROOT / "examples" / "demo.py"
    if demo.exists():
        run([str(py), str(demo)])
        ok("Библиотека работает")
    else:
        warn("examples/demo.py не найден — пропускаю")

    header("CLI-демо Document Agent (4 кейса §12)")
    main_py = PROJECT_ROOT / "main.py"
    if main_py.exists():
        run([str(py), str(main_py), "--cli"])
        ok("Document Agent: 4 кейса прошли")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="build.py",
        description="Сборка Document Agent + agentic_guardrails",
    )
    parser.add_argument("--no-test", action="store_true", help="только сборка")
    parser.add_argument("--no-demo", action="store_true", help="без smoke/CLI")
    parser.add_argument("--no-cov", action="store_true", help="без покрытия")
    parser.add_argument("--clean", action="store_true", help="снести .venv и собрать с нуля")
    args = parser.parse_args()

    if args.clean:
        header("Очистка")
        if VENV_DIR.exists():
            shutil.rmtree(VENV_DIR)
            ok(f"Удалено: {VENV_DIR}")
        else:
            ok("Нечего удалять")

    check_python()
    py = ensure_venv()
    install_deps(py)
    check_imports(py)
    tests = run_tests(py, args.no_test)
    run_coverage(py, tests, args.no_cov)
    run_demo(py, args.no_demo)

    header("Готово")
    ok("Проект собран и протестирован")
    print()
    print("  Активировать окружение:")
    if platform.system() == "Windows":
        print("    .venv\\Scripts\\activate")
    else:
        print("    source .venv/bin/activate")
    print("  Веб-интерфейс:     python main.py")
    print("  CLI-демо:          python main.py --cli")
    print("  Пересобрать:       python build.py")
    print("  С нуля:            python build.py --clean")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
