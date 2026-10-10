# agentic_guardrails

Библиотека универсальных предохранителей для систем на основе искусственного интеллекта.

Три критичных модуля написаны на языке Rust и вызываются из Python через PyO3. Остальные модули остаются на Python.

В комплекте — компоненты оркестратора (`orchestrator_components.py`): шина событий, базовые агенты, оркестратор workflow. Это переиспользуемые кирпичи, а не полное демо.

## Что внутри

### На Rust (через PyO3)

| Модуль | Назначение |
|---|---|
| `cost_cb` | Счётчик бюджета токенов. Три уровня: задача, сессия, горизонт. Атомарные счётчики — состояние гонок исключено. Есть пакетный режим. |
| `failure_cb` | Предохранитель от каскадных сбоев. Состояния: закрыт, открыт, полуоткрыт. Атомарный автомат состояний. |
| `audit` | Неизменяемый журнал с цепочкой хешей. Каждая запись содержит отпечаток предыдущей. Подделка обнаруживается. Есть пакетный режим. |

### На Python

| Модуль | Назначение |
|---|---|
| `policy_hook_bus` | Шина правил. Проверяет действие до его выполнения. Асинхронные правила. Ограничение времени на правило. |
| `saga` | Компенсация при сбоях в цепочке шагов. Откат в обратном порядке. Повторы компенсаций. Флаг неконсистентности. |
| `hitl` | Лестница автономии. Уровни от полного контроля человеком до полного доверия. Ограничение времени на ответ. |
| `evidence` | Паспорт завершённой задачи. Что сделано, какие решения приняты. |
| `retry_policy` | Повторы асинхронных вызовов. Настраиваемый backoff, jitter, отбор исключений. |
| `rate_limiter` | Ограничение частоты вызовов. Ведро токенов, пакетное списание, режим без ожидания. |
| `parallel_runner` | Параллельное выполнение задач с ограничением одновременности. Изоляция сбоев. |

### Компоненты оркестратора

Файл `orchestrator_components.py` в корне — готовые компоненты для сборки агентной системы. Используют модули библиотеки.

| Компонент | Назначение |
|---|---|
| `EventBus` | Шина событий (симуляция Kafka). Публикация и подписка, конверт с `correlation_id`, повторы подписчиков, ограничение времени, запись в аудит. |
| `LLMClient` | Обёртка над вызовом модели. Ограничитель частоты и повторы. Не привязан к провайдеру — принимает асинхронную функцию. |
| `BaseAgent` | Базовый агент. Предохранитель от сбоев, бюджет токенов, публикация результата в шину. |
| `ScoringAgent` | Агент скоринга. |
| `ComplianceAgent` | Агент комплаенса. Использует шину правил. |
| `GuardianAgent` | Проверка поведения и персональных данных. Использует шину правил. |
| `ReviewAgent` | Финальное ревью. Использует лестницу автономии. |
| `OrchestratorAgent` | Оркестратор workflow. Согласованное выполнение шагов через сагу, ожидание ответов агентов по `correlation_id`, бюджет, аудит. |

## Требования

- Python 3.10 или новее
- Rust 1.63 или новее
- Компилятор C (для линковки)
- Модуль `python3-venv`

## Сборка

Одна команда — от нуля до работающей библиотеки:

```
./build.sh
```

Скрипт сам:
1. Проверяет окружение
2. Создаёт виртуальное окружение
3. Ставит зависимости Python
4. Собирает ядро на Rust
5. Прогоняет тесты Rust
6. Прогоняет тесты Python

Полезные флаги:

| Флаг | Действие |
|---|---|
| `--no-test` | Только сборка, без тестов |
| `--clean` | Снести `target/` и `.venv/`, собрать с нуля |
| `--fast` | Отладочная сборка: быстрее компиляция, медленнее выполнение |
| `--help` | Показать справку |

## Быстрый старт

После сборки активируйте окружение:

```
source .venv/bin/activate
```

```python
from agentic_guardrails import (
    CostBreaker, Budget,
    RateLimiter,
    RetryPolicy,
    parallel,
)

# Бюджет токенов
budget = CostBreaker(
    per_task=Budget(limit=10_000),
    per_session=Budget(limit=100_000),
    per_horizon=Budget(limit=1_000_000),
)

# Ограничение частоты
limiter = RateLimiter(max_tokens=100, refill_rate=10.0)
await limiter.acquire()

# Повторы
policy = RetryPolicy(max_attempts=3, retry_on=(TimeoutError,))
result = await policy.execute(call_llm, prompt="...")

# Параллельно
results = await parallel([task_a, task_b, task_c], max_concurrency=3)
```

## Использование

### Счётчик бюджета токенов

```python
from agentic_guardrails.cost_cb import CostBreaker, Budget

счётчик = CostBreaker(
    per_task=Budget(limit=10_000),
    per_session=Budget(limit=100_000),
    per_horizon=Budget(limit=1_000_000),
)

состояние = счётчик.charge(500)
# состояние: CostState.OK, CostState.SOFT или CostState.HARD
```

Пакетный режим — один вызов вместо тысячи:

```python
состояние = счётчик.charge_batch([100] * 1000)
```

### Предохранитель от сбоев

```python
from agentic_guardrails.failure_cb import FailureBreaker, CBState

предохранитель = FailureBreaker(
    failure_threshold=5,
    recovery_timeout=30.0,
    half_open_probes=1,
)

if предохранитель.allow():
    try:
        результат = внешний_вызов()
        предохранитель.on_success()
    except Exception:
        предохранитель.on_failure()
```

### Журнал аудита

```python
from agentic_guardrails.audit import AuditChain

журнал = AuditChain()
журнал.append("решение_по_кредиту", {"номер_заявки": "т-42", "решение": "одобрено"})
журнал.append("эскалация", {"номер_заявки": "т-42", "причина": "сумма выше порога"})

assert журнал.verify() is True
assert len(журнал) == 2
```

Пакетная запись:

```python
журнал.append_batch([
    ("событие_1", {"данные": 1}),
    ("событие_2", {"данные": 2}),
])
```

### Шина правил

```python
from agentic_guardrails.policy_hook_bus import HookBus, PolicyResult, Decision

шина = HookBus(timeout_per_policy=0.1)

async def правило_без_пди(действие, контекст):
    if контекст.get("пди"):
        return PolicyResult(Decision.DENY, reason="обнаружены ПДн", policy_id="no_pii")
    return PolicyResult(Decision.ALLOW)

шина.register("no_pii", правило_без_пди)

результат = await шина.check("отправить_письмо", {"пди": False})
assert результат.decision is Decision.ALLOW
```

### Компенсация при сбоях

```python
from agentic_guardrails.saga import SagaStep, run_saga

async def списать(ctx): return {"сумма": 100}
async def вернуть(ctx, out): pass
async def упасть(ctx): raise RuntimeError("сбой")

результат = await run_saga([
    SagaStep("списать", списать, вернуть),
    SagaStep("уведомить", упасть, lambda c, o: None),
])

assert результат.success is False
assert результат.failed_at == "уведомить"
assert результат.compensation_errors == []
assert результат.inconsistent is False
```

### Лестница автономии

```python
from agentic_guardrails.hitl import AutonomyLadder, RLevel, run_with_approval

лестница = AutonomyLadder(nominal=RLevel.R5, approval_timeout=30.0)

решение = лестница.decide(criticality=1, risk=1, complexity=0)
# решение.level -> RLevel.R3
# решение.reason -> строка с обоснованием

async def одобрить(действие):
    return True

разрешено = await run_with_approval(
    лестница, {"действие": "выпуск"}, одобрить,
    ctx={"criticality": 1, "risk": 1, "complexity": 0},
)
```

### Повторы

```python
from agentic_guardrails.retry_policy import (
    RetryPolicy, FixedBackoff, ExponentialBackoff, retry,
)

политика = RetryPolicy(
    max_attempts=3,
    backoff=ExponentialBackoff(base=0.1, factor=2.0, jitter=True),
    retry_on=(TimeoutError, ConnectionError),
    give_up_on=(ValueError,),
)

@retry(политика)
async def вызвать_модель(запрос):
    ...

# Или напрямую
результат = await политика.execute(вызвать_модель, запрос="...")
```

### Ограничение частоты

```python
from agentic_guardrails.rate_limiter import RateLimiter

ограничитель = RateLimiter(max_tokens=100, refill_rate=10.0)

# Способ 1: явное ожидание
await ограничитель.acquire()
await ограничитель.acquire(cost=5)

# Способ 2: контекстный менеджер
async with ограничитель:
    await вызвать_api()

# Способ 3: декоратор
@ограничитель.wrap
async def вызвать_api():
    ...

# Способ 4: без ожидания
if ограничитель.try_acquire():
    await вызвать_api()
```

### Параллельные задачи

```python
from agentic_guardrails.parallel_runner import parallel, ParallelRunner

результаты = await parallel([
    lambda: получить_a(),
    lambda: получить_b(),
    (получить_c, "аргумент"),
], max_concurrency=3, timeout_per_task=5.0)

for r in результаты:
    if r.success:
        print(r.value)
    else:
        print(f"ошибка: {r.error}")
```

### Компоненты оркестратора

Компоненты — переиспользуемые кирпичи. Сценарий собирается вручную.

```python
from orchestrator_components import (
    EventBus, OrchestratorAgent,
    ScoringAgent, ComplianceAgent, ReviewAgent,
)

# Шина и агенты
bus = EventBus()
scoring = ScoringAgent(name="scoring", bus=bus)
compliance = ComplianceAgent(name="compliance", bus=bus)
review = ReviewAgent(name="review", bus=bus)

# Подписки: агенты слушают команды, публикуют результат в agent.completed
bus.subscribe("scoring.requested", "scoring", scoring.handle)
bus.subscribe("compliance.requested", "compliance", compliance.handle)
bus.subscribe("review.requested", "review", review.handle)

# Оркестратор подписывается на agent.completed сам, внутри __post_init__
orchestrator = OrchestratorAgent(bus=bus)

# Запуск workflow: scoring -> compliance -> review (последовательно)
result = await orchestrator.run_workflow(amount=1_000_000)
```

**Ключевое:** `OrchestratorAgent` ждёт ответа от каждого агента по `correlation_id`. Если агент не ответил за `step_timeout` — `asyncio.TimeoutError`, сработает компенсация саги.

## Устройство проекта

```
agentic_guardrails/
├── build.sh                       — скрипт сборки
├── Cargo.toml                     — описание пакета Rust и рабочего пространства
├── pyproject.toml                 — описание пакета Python и настройки сборки
├── orchestrator_components.py     — компоненты оркестратора (шина, агенты, оркестратор)
├── crates/                        — ядро на Rust
│   ├── cost_cb/                   — счётчик бюджета токенов
│   ├── failure_cb/                — предохранитель от сбоев
│   └── audit/                     — журнал аудита
├── src/
│   └── lib.rs                     — слой связи с Python (PyO3)
├── agentic_guardrails/            — пакет Python
│   ├── cost_cb.py                 — обёртка над ядром
│   ├── failure_cb.py              — обёртка над ядром
│   ├── audit.py                   — обёртка над ядром
│   ├── policy_hook_bus.py         — шина правил
│   ├── saga.py                    — компенсация
│   ├── hitl.py                    — лестница автономии
│   ├── evidence.py                — паспорт задачи
│   ├── retry_policy.py            — повторы
│   ├── rate_limiter.py            — ограничение частоты
│   └── parallel_runner.py         — параллельные задачи
├── examples/
│   └── demo.py                    — проверка всех модулей
├── test_guardrails.py             — основной набор тестов
├── test_retry_policy.py           — тесты повторов
├── test_rate_limiter.py           — тесты ограничителя частоты
├── test_parallel_runner.py        — тесты параллельного запуска
└── test_properties.py             — тесты на основе свойств
```

## Тесты

Основной набор — в файле `test_guardrails.py`. Три уровня:

- Модульные — каждый модуль в отдельности
- Связующие — несколько модулей вместе
- Сквозные — полный проход задачи через все модули

Отдельные файлы для новых модулей и тестов на основе свойств.

Запуск всех тестов:

```
pytest -v
```

Запуск без сквозных тестов (быстрее):

```
pytest -v -m "not e2e"
```

Проверка покрытия:

```
pytest --cov=agentic_guardrails --cov-report=term-missing
```

Тесты ядра на Rust:

```
cargo test --workspace
```

Проверка, что все модули работают:

```
python examples/demo.py
```

## Как устроена связь Python и Rust

Каждый модуль на Rust собирается в отдельный пакет. Слой связи (`src/lib.rs`) регистрирует их в Python под общим именем `_core`. Обёртки на Python импортируют классы из `_core` и предоставляют тот же программный интерфейс, что был раньше.

Пользователь не знает, что под капотом Rust. Он импортирует `CostBreaker` и вызывает `charge()`. Всё остальное — детали реализации.

## Известные особенности

- Поле `timestamp` в журнале аудита хранится как целое число (миллисекунды с начала эпохи). Наружу отдаётся строкой в формате ISO 8601.
- Поле `payload` в записи журнала возвращается копией. Изменение копии из Python не влияет на состояние журнала.
- Сборка рассчитана на Rust 1.63 и новее. Пакет `time` не используется намеренно: свежие его версии требуют более нового компилятора.
- В `RetryPolicy` поле `FixedBackoff` называется `interval`, а метод — `delay`. Это сделано, чтобы избежать конфликта имён поле/метод в `dataclass`.
- `OrchestratorAgent` подписывается на `agent.completed`, `agent.failed`, `agent.blocked`. Ключ ожидания — пара `(correlation_id, имя агента)`. Если два агента ответят с одним `correlation_id`, будет использован первый; второй проигнорируется.

## Лицензия

MIT

## Известные ограничения и потенциальные баги

Раздел для себя: что нужно помнить при использовании.

### Ограничения

**Числа в payload журнала аудита** — в пределах i64
- `serde_json` не принимает числа за пределами `-2^63 .. 2^63-1`
- Python `int` безграничен, поэтому `chain.append("e", {"id": 2**70})` упадёт
- Обходной путь: передавать большие идентификаторы **строками** — `str(id)`

**Timestamp в журнале аудита** — Unix millis (целое)
- Внутри Rust — `u64` миллисекунд
- Снаружи Python — ISO-строка (`AuditEntry.timestamp`)
- Сырое значение — `AuditEntry.timestamp_millis`

**Payload журнала возвращается копией**
- `chain.entries[0].payload` — **новый** объект при каждом вызове
- Мутация копии не влияет на Rust-цепочку
- Это **правильно** для безопасности, но нужно знать

**Пакетная запись в журнал (append_batch)**
- Валидация payload остаётся рекурсивной — на очень больших пачках заметно
- Пакет не отменяет проверку: если один payload невалиден, вся пачка отклоняется
- Выигрыш по скорости от пакета скромный (~1.3×), потому что большая часть времени уходит на Python-цикл и `depythonize`, а не на переход в Rust

**Повторы (retry_policy)**
- Работают только с асинхронными функциями
- Для синхронного кода нужна отдельная обёртка (не сделана)
- Backoff завязан на реальное время — тесты используют маленькие задержки

**Ограничитель частоты (rate_limiter)**
- Состояние — в памяти процесса
- Несколько реплик агента **не разделяют** лимит
- Для распределённого сценария — **нужен** внешний backend (Redis)

**Параллельные задачи (parallel_runner)**
- Использует `asyncio.gather` с семафором
- Блокирующие синхронные вызовы внутри задач **заблокируют** цикл событий
- Для CPU-тяжёлых задач нужен `run_in_executor`

**Шина событий (EventBus)**
- Симуляция Kafka: сообщения хранятся в памяти процесса
- Нет персистентности между перезапусками
- Нет гарантии порядка сообщений при параллельной публикации
- Нет групп потребителей (consumer groups)
- Для продакшна — **нужна** настоящая шина (Kafka, NATS, RabbitMQ)

**Оркестратор (OrchestratorAgent)**
- Состояние `_pending` — в памяти процесса
- При перезапуске оркестратора незавершённые workflow теряются
- Один `correlation_id` = один активный workflow. Параллельные workflow с одним `correlation_id` конфликтуют
- Ожидание ответа по паре `(correlation_id, agent)`. Если агент публикует результат с другим `agent`, оркестратор его не увидит
- `step_timeout` — общий для всех шагов. Разные таймауты на шаг не поддерживаются

### Потенциальные баги (не покрыты тестами)

**Конкурентность Python-части**
- `CostBreaker`, `FailureBreaker`, `AuditChain` — **на Rust**, гонок нет by design
- Но `policy_hook_bus`, `saga`, `hitl`, `rate_limiter`, `parallel_runner` — **на Python**
- Параллельные вызовы из нескольких asyncio-задач **не тестировались** под нагрузкой
- В однопоточном asyncio — **безопасно**; в многопоточном — **проверить**

**Гонка в `PolicyHookBus.register`**
- `register` модифицирует список без блокировки
- Если несколько задач регистрируют правила **одновременно** — **возможна** потеря
- Обход: **все** правила регистрировать **до** первого `check`

**`Saga` с параллельными шагами**
- Сейчас шаги выполняются **последовательно**
- Если понадобится **параллельный** запуск — компенсации усложнятся

**Оркестратор и параллельные workflow**
- `_pending` — один словарь на оркестратор
- Два workflow с одним `correlation_id` **перезапишут** Future друг друга
- Обход: **всегда** новый `correlation_id` для каждого workflow

**Двойная доставка сообщения**
- Если агент опубликует `agent.completed` дважды с одним `correlation_id`, оркестратор **проигнорирует** второй ответ
- Это **правильно** (idempotency), но нужно знать

**Распределённые системы**
- Всё состояние — **в памяти процесса**
- Несколько реплик агента **не разделяют** бюджеты и журнал
- Для распределённого сценария — **нужен** внешний backend (Redis, S3)

### Что не реализовано (сознательно)

**Persistence**
- `AuditChain.save(path)` / `.load(path)` — не сделано
- `CostBreaker` не сохраняет бюджеты между перезапусками
- `EventBus` не сохраняет сообщения между перезапусками
- `OrchestratorAgent` не восстанавливает workflow после падения
- Для долгоживущих процессов — **нужно** добавить

**Наблюдаемость**
- Нет счётчиков, таймингов, метрик
- Для продакшна — **нужно** добавить (Prometheus, OpenTelemetry)

**Бюджет на агента**
- Один `CostBreaker` — один бюджет
- Для мультиагентной системы — **нужен** per-agent бюджет

**Синхронные версии**
- Все новые модули (`retry_policy`, `rate_limiter`, `parallel_runner`) — только асинхронные
- Для вызова из синхронного кода нужна обёртка

**Настоящая шина сообщений**
- `EventBus` — симуляция для прототипа
- Для продакшна — **нужна** Kafka / NATS / RabbitMQ
- Модуль `agentic_guardrails` **не** содержит интеграции с шиной — это **внешняя** инфраструктура

### Что стоит проверить перед серьёзным использованием

- **Нагрузка** — 1000+ заявок/сек, где узкое место?
- **Конкурентность** — многопоточные сценарии
- **Восстановление** — что происходит при перезапуске процесса?
- **Пакетная запись в журнал** — даёт ли выигрыш на вашем размере пачек?
- **Оркестратор под нагрузкой** — что при 100+ параллельных workflow?
