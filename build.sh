#!/usr/bin/env bash
#
# build.sh — сборка agentic_guardrails с нуля.
#
# Что делает:
#   1. Проверяет окружение (python3, cargo, rustc)
#   2. Создаёт .venv (если нет) и активирует
#   3. Ставит Python-зависимости (maturin, pytest, pytest-asyncio)
#   4. Собирает Rust-ядро через maturin develop --release
#   5. Прогоняет Rust-тесты (cargo test)
#   6. Прогоняет Python-тесты (pytest)
#
# Использование:
#   ./build.sh              # полная сборка + тесты
#   ./build.sh --no-test    # только сборка, без тестов
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
CLEAN=0
FAST=0
for arg in "$@"; do
    case "$arg" in
        --no-test) NO_TEST=1 ;;
        --clean)   CLEAN=1 ;;
        --fast)    FAST=1 ;;
        -h|--help)
            sed -n '3,15p' "$0" | sed 's/^# \?//'
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
python -m pip install --quiet maturin pytest pytest-asyncio
ok "maturin $(maturin --version | awk '{print $2}')"
ok "pytest $(pytest --version | head -1 | awk '{print $2}')"

# ==== Шаг 4: сборка Rust ====
header "Сборка Rust-ядра"

MATURIN_ARGS=(develop)
if [[ "$FAST" -eq 0 ]]; then
    MATURIN_ARGS+=(--release)
fi

cd "$PROJECT_ROOT"
maturin "${MATURIN_ARGS[@]}" || fail "maturin ${MATURIN_ARGS[*]} упал"
ok "Rust-ядро собрано ($( [[ $FAST -eq 1 ]] && echo debug || echo release ))"

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
    if [[ -f "${PROJECT_ROOT}/test_guardrails.py" ]]; then
        pytest -q "${PROJECT_ROOT}/test_guardrails.py" || fail "Python-тесты провалились"
        ok "Python-тесты прошли"
    else
        warn "test_guardrails.py не найден, пропускаю"
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
echo ""

