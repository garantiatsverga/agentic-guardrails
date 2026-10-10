"""Event Bus — симуляция Kafka/RabbitMQ для прототипа оркестратора.

Интеграция с agentic_guardrails:
- retry_policy   — повторы при временных сбоях подписчиков
- parallel_runner— параллельная рассылка с таймаутом
- audit          — каждое сообщение в tamper-evident журнал
- failure_cb     — защита подписчиков от каскадных сбоев

Пример:
    bus = EventBus()

    @bus.on("command")
    async def handler(envelope):
        ...

    await bus.publish("command", {"event_type": "scoring.start"})
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List

import asyncio
import uuid

from agentic_guardrails.audit import AuditChain
from agentic_guardrails.parallel_runner import parallel, TaskResult
from agentic_guardrails.retry_policy import RetryPolicy, ExponentialBackoff


Handler = Callable[[dict], Awaitable[Any]]


@dataclass
class EventBus:
    """Простейший Event Bus (Топик-брокер) с интеграцией предохранителей."""

    retry_attempts: int = 3
    publish_timeout: float = 5.0
    max_concurrency: int = 10
    audit_enabled: bool = True

    _subscribers: Dict[str, List[Handler]] = field(default_factory=dict)
    _message_log: List[dict] = field(default_factory=list)
    _audit: AuditChain = field(default_factory=AuditChain)
    _retry: RetryPolicy = field(init=False)

    def __post_init__(self) -> None:
        self._retry = RetryPolicy(
            max_attempts=self.retry_attempts,
            backoff=ExponentialBackoff(base=0.01, factor=2.0, jitter=True),
            retry_on=(TimeoutError, ConnectionError),
        )

    # ==== Подписка ====

    def subscribe(self, topic: str, callback: Handler) -> None:
        """Подписать обработчик на топик."""
        self._subscribers.setdefault(topic, []).append(callback)
        name = getattr(callback, "__name__", repr(callback))
        print(f"[Broker] {name} подписался на '{topic}'")

    def on(self, topic: str):
        """Декоратор для подписки."""
        def decorator(fn: Handler) -> Handler:
            self.subscribe(topic, fn)
            return fn
        return decorator

    # ==== Публикация ====

    async def publish(self, topic: str, payload: dict) -> List[TaskResult]:
        """Опубликовать сообщение в топик.

        Возвращает список TaskResult (по одному на подписчика).
        Каждый подписчик вызывается с retry на TimeoutError/ConnectionError.
        """
        envelope = self._make_envelope(topic, payload)
        self._message_log.append(envelope)

        if self.audit_enabled:
            self._audit.append("message.published", {
                "topic": topic,
                "message_id": envelope["meta"]["message_id"],
                "correlation_id": envelope["meta"]["correlation_id"],
                "event_type": payload.get("event_type"),
            })

        event_type = payload.get("event_type", "<no-event-type>")
        print(f"\n[Broker] -> '{topic}': {event_type}")

        handlers = self._subscribers.get(topic, [])
        if not handlers:
            print(f"[Broker]   нет подписчиков на '{topic}'")
            return []

        # Параллельная рассылка с таймаутом и изоляцией сбоев
        tasks = [(self._call_with_retry, h, envelope) for h in handlers]
        results = await parallel(
            tasks,
            max_concurrency=self.max_concurrency,
            timeout_per_task=self.publish_timeout,
        )

        for r in results:
            h = handlers[r.index]
            name = getattr(h, "__name__", repr(h))
            if r.success:
                print(f"[Broker]   ✓ {name}")
            else:
                print(f"[Broker]   ✗ {name}: {type(r.error).__name__}: {r.error}")

        return results

    async def _call_with_retry(self, handler: Handler, envelope: dict) -> Any:
        """Вызов одного подписчика с retry."""
        return await self._retry.execute(handler, envelope)

    # ==== Envelope ====

    @staticmethod
    def _make_envelope(topic: str, payload: dict) -> dict:
        """Формирует конверт с метаданными."""
        return {
            "meta": {
                "message_id": str(uuid.uuid4()),
                "correlation_id": payload.get("correlation_id", str(uuid.uuid4())),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "topic": topic,
            },
            "payload": payload,
        }

    @staticmethod
    def reply_correlation(envelope: dict) -> str:
        """Извлекает correlation_id из конверта для ответа."""
        return envelope["meta"]["correlation_id"]

    # ==== Диагностика ====

    @property
    def message_log(self) -> List[dict]:
        """Все опубликованные сообщения (имитация Kafka-хранилища)."""
        return list(self._message_log)

    @property
    def audit_chain(self) -> AuditChain:
        """Журнал аудита всех публикаций."""
        return self._audit

    def stats(self) -> dict:
        """Сводка по шине."""
        return {
            "topics": list(self._subscribers.keys()),
            "subscribers": {t: len(hs) for t, hs in self._subscribers.items()},
            "messages": len(self._message_log),
            "audit_ok": self._audit.verify(),
        }
