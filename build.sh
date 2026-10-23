#!/usr/bin/env bash
#
# build.sh — сборка agentic_guardrails с нуля.
#
# Что делает:
#   1. Проверяет окружение (python3, cargo, rustc)
#   2. Создаёт .venv (если нет) и активирует
#   3. Ставит Python-зависимости (maturin, pytest, pytest-asyncio, pytest-cov, hypothesis)
#   4. Собирает Rust-ядро через maturin develop --release
#   5. Прогоняет Rust-тесты (cargo test --workspace)
#   6. Прогоняет Python-тесты (pytest)
#   7. Прогоняет smoke test (examples/demo.py)
#   8. Показывает покрытие (pytest --cov)
#
# Использование:
#   ./build.sh              # полная сборка + тесты + покрытие + demo
#   ./build.sh --no-test    # только сборка
#   ./build.sh --no-demo    # без smoke-теста
#   ./build.sh --no-cov     # без покрытия
#   ./build.sh --clean      # снести target/ и .venv/, собрать с нуля
#   ./build.sh --fast       # debug-сборка (быстрее, но медленнее runtime)
#
set -euo pipefail

# ==== Конфигурация ====
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
PYTHON_MIN_MAJOR=3
PYTHON_MIN_MINOR=10
RUST_MIN_MAJOR=1
RUST_MIN_MINOR=63

# Все Python-зависимости для разработки и тестов
PY_DEPS=(
    "maturin"
    "pytest"
    "pytest-asyncio"
    "pytest-cov"
    "hypothesis"
)

# Файлы Python-тестов (явно, без автопоиска — чтобы CI был предсказуем)
PY_TEST_FILES=(
    "tests/test_guardrails.py"
    "tests/test_retry_policy.py"
    "tests/test_rate_limiter.py"
    "tests/test_parallel_runner.py"
    "tests/test_orchestrator.py"
)

PY_PROPERTY_TEST="tests/test_properties.py"

# ==== Цвета ====
if [[ -t 1 ]]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
    BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; BOLD=''; NC=''
fi

log()   { echo -e "${BLUE}▸${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
fail()  { echo -e "${RED}✗${NC} $*" >&2; exit 1; }
header(){ echo -e "\n${BOLD}=== $* ===${NC}"; }

# ==== Парсинг аргументов ====
NO_TEST=0
NO_DEMO=0
NO_COV=0
CLEAN=0
FAST=0
for arg in "$@"; do
    case "$arg" in
        --no-test) NO_TEST=1 ;;
        --no-demo) NO_DEMO=1 ;;
        --no-cov)  NO_COV=1 ;;
        --clean)   CLEAN=1 ;;
        --fast)    FAST=1 ;;
        -h|--help)
            sed -n '3,22p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) fail "Неизвестный аргумент: $arg (см. --help)" ;;
    esac
done

# ==== Шаг 0: опциональная очистка ====
if [[ "$CLEAN" -eq 1 ]]; then
    header "Очистка"
    log "Удаляю target/..."
    rm -rf "${PROJECT_ROOT}/target"
    log "Удаляю .venv/..."
    rm -rf "${VENV_DIR}"
    ok "Очищено"
fi

# ==== Шаг 1: проверка окружения ====
header "Проверка окружения"

command -v python3 >/dev/null 2>&1 || fail "python3 не найден в PATH"
command -v cargo   >/dev/null 2>&1 || fail "cargo не найден в PATH (установи Rust: https://rustup.rs)"
command -v rustc   >/dev/null 2>&1 || fail "rustc не найден в PATH"

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

if [[ "$PY_MAJOR" -lt "$PYTHON_MIN_MAJOR" ]] || \
   { [[ "$PY_MAJOR" -eq "$PYTHON_MIN_MAJOR" ]] && [[ "$PY_MINOR" -lt "$PYTHON_MIN_MINOR" ]]; }; then
    fail "Требуется Python >= ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}, найден ${PY_VER}"
fi

RUSTC_VERSION=$(rustc --version | awk '{print $2}')
RUSTC_MAJOR=$(echo "$RUSTC_VERSION" | cut -d. -f1)
RUSTC_MINOR=$(echo "$RUSTC_VERSION" | cut -d. -f2)

if [[ "$RUSTC_MAJOR" -lt "$RUST_MIN_MAJOR" ]] || \
   { [[ "$RUSTC_MAJOR" -eq "$RUST_MIN_MAJOR" ]] && [[ "$RUSTC_MINOR" -lt "$RUST_MIN_MINOR" ]]; }; then
    fail "Требуется Rust >= ${RUST_MIN_MAJOR}.${RUST_MIN_MINOR}, найден ${RUSTC_VERSION}"
fi

ok "python3 ${PY_VER}"
ok "rustc ${RUSTC_VERSION}"
ok "cargo $(cargo --version | awk '{print $2}')"

# ==== Шаг 2: venv ====
header "Виртуальное окружение"

if [[ ! -d "$VENV_DIR" ]]; then
    log "Создаю ${VENV_DIR}..."
    python3 -m venv "$VENV_DIR" || fail "python3 -m venv не сработал (нужен python3-venv?)"
    ok "venv создан"
else
    ok "venv уже существует"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate" || fail "Не удалось активировать venv"
ok "venv активирован: $(which python)"

# ==== Шаг 3: зависимости ====
header "Установка Python-зависимостей"

python -m pip install --quiet --upgrade pip
python -m pip install --quiet "${PY_DEPS[@]}"

for dep in "${PY_DEPS[@]}"; do
    ver=$(python -m pip show "$dep" 2>/dev/null | awk '/^Version:/ {print $2}')
    ok "${dep} ${ver}"
done

# ==== Шаг 4: сборка Rust ====
header "Сборка Rust-ядра"

MATURIN_ARGS=(develop)
if [[ "$FAST" -eq 0 ]]; then
    MATURIN_ARGS+=(--release)
fi

cd "$PROJECT_ROOT"
maturin "${MATURIN_ARGS[@]}" || fail "maturin ${MATURIN_ARGS[*]} упал"
ok "Rust-ядро собрано ($( [[ $FAST -eq 1 ]] && echo debug || echo release ))"

# ==== Шаг 4.5: проверка импорта компонентов оркестратора ====
if [[ -f "${PROJECT_ROOT}/orchestrator_components.py" ]]; then
    header "Проверка компонентов оркестратора"
    python -c "
import sys
sys.path.insert(0, '${PROJECT_ROOT}')
import orchestrator_components
print('OK')
" || fail "orchestrator_components.py не импортируется"
    ok "orchestrator_components.py импортируется"
fi

# ==== Шаг 4.6: проверка импорта библиотеки ====
header "Проверка импорта библиотеки"
python -c "import agentic_guardrails; print('agentic_guardrails OK')" \
    || fail "agentic_guardrails не импортируется"
ok "agentic_guardrails импортируется"

# ==== Шаг 5: Rust-тесты ====
if [[ "$NO_TEST" -eq 0 ]]; then
    header "Rust-тесты"
    cargo test --workspace --quiet 2>&1 | grep -E "^(running|test result)" || true
    if cargo test --workspace --quiet 2>&1 | grep -q "FAILED"; then
        fail "Rust-тесты провалились"
    fi
    ok "Rust-тесты прошли"

    # ==== Шаг 6: Python-тесты ====
    header "Python-тесты"

    existing_tests=()
    for f in "${PY_TEST_FILES[@]}"; do
        if [[ -f "${PROJECT_ROOT}/${f}" ]]; then
            existing_tests+=("$f")
        else
            warn "Пропускаю отсутствующий ${f}"
        fi
    done

    if [[ ${#existing_tests[@]} -eq 0 ]]; then
        warn "Не найдено ни одного тестового файла — пропускаю"
    else
        pytest -q "${existing_tests[@]}" || fail "Основные тесты провалились"
        ok "Основные тесты прошли"
    fi

    # Property-based тесты отдельно
    if [[ -f "${PROJECT_ROOT}/${PY_PROPERTY_TEST}" ]]; then
        log "Property-based тесты (${PY_PROPERTY_TEST})..."
        pytest -q "${PY_PROPERTY_TEST}" || fail "Property-based тесты провалились"
        ok "Property-based тесты прошли"
    fi
fi

# ==== Шаг 7: покрытие ====
if [[ "$NO_TEST" -eq 0 && "$NO_COV" -eq 0 ]]; then
    header "Покрытие"
    if [[ ${#existing_tests[@]} -gt 0 ]]; then
        pytest --quiet --cov=agentic_guardrails --cov-report=term-missing \
            "${existing_tests[@]}" 2>&1 | tail -20
    fi
fi

# ==== Шаг 8: smoke test ====
if [[ "$NO_TEST" -eq 0 && "$NO_DEMO" -eq 0 ]]; then
    header "Smoke test (examples/demo.py)"
    if [[ -f "${PROJECT_ROOT}/examples/demo.py" ]]; then
        python "${PROJECT_ROOT}/examples/demo.py" || fail "Smoke test провалился"
        ok "Все модули работают"
    else
        warn "examples/demo.py не найден — пропускаю"
    fi
fi

# ==== Финал ====
header "Готово"
ok "Проект собран и протестирован"
echo ""
echo "  Активировать окружение:  source .venv/bin/activate"
echo "  Пересобрать:             ./build.sh"
echo "  Быстрая сборка:          ./build.sh --fast"
echo "  С нуля:                  ./build.sh --clean"
echo "  Без покрытия:            ./build.sh --no-cov"
echo "  Без demo:                ./build.sh --no-demo"
echo ""
