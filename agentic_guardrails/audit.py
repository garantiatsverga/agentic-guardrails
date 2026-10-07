"""Append-only Audit Chain с hash-chain — обёртка над Rust-ядром.

API 1:1 с прежней версией. timestamp снаружи — ISO-строка,
внутри Rust — Unix millis (u64).
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

from ._core import AuditChain as _RustAuditChain
from ._core import AuditEntry as _RustAuditEntry


class AuditEntry:
    """Обёртка над Rust AuditEntry. timestamp конвертируется в ISO."""

    def __init__(self, inner: _RustAuditEntry) -> None:
        self._inner = inner

    @property
    def seq(self) -> int:
        return self._inner.seq

    @property
    def prev_hash(self) -> str:
        return self._inner.prev_hash

    @property
    def timestamp(self) -> str:
        """ISO 8601 (UTC) из Unix millis."""
        dt = datetime.fromtimestamp(self._inner.timestamp / 1000, tz=timezone.utc)
        return dt.isoformat()

    @property
    def timestamp_millis(self) -> int:
        """Unix millis (raw)."""
        return self._inner.timestamp

    @property
    def event(self) -> str:
        return self._inner.event

    @property
    def payload(self) -> Any:
        """НОВЫЙ объект. Мутация не влияет на Rust-структуру."""
        return self._inner.payload()

    @property
    def entry_hash(self) -> str:
        return self._inner.entry_hash

    def __repr__(self) -> str:
        return (
            f"AuditEntry(seq={self.seq}, event={self.event!r}, "
            f"timestamp={self.timestamp!r})"
        )


class AuditChain:
    """Append-only цепочка с hash-chain.

    Каждая запись содержит sha256 от {seq, prev_hash, timestamp, event, payload}.
    Изменение любой записи ломает verify().
    """

    def __init__(self) -> None:
        self._inner = _RustAuditChain()

    def append(self, event: str, payload: Any) -> AuditEntry:
        """Добавляет запись.

        payload — любой JSON-сериализуемый объект. Числа — в пределах
        i64 (-2^63 .. 2^63-1) или float64. Большие числа передавайте
        строками.

        Бросает ValueError, если payload не сериализуется.
        """
        _validate_payload(payload)
        return AuditEntry(self._inner.append(event, payload))

    def append_batch(
        self,
        events: list[tuple[str, Any]],
    ) -> list[AuditEntry]:
        """Добавляет пачку записей за один вызов.

        Эквивалент N × append, но с одним переходом Python<->Rust.
        Валидация — быстрая (только int на верхнем уровне dict).
        Если один payload невалиден — вся пачка отклоняется.

        Возвращает список AuditEntry в порядке входа.
        """
        for _, payload in events:
            _validate_payload(payload)
        return [AuditEntry(e) for e in self._inner.append_batch(events)]

    def verify(self) -> bool:
        return self._inner.verify()

    @property
    def entries(self) -> list[AuditEntry]:
        return [AuditEntry(e) for e in self._inner.entries()]

    def __len__(self) -> int:
        return len(self._inner)

    def __repr__(self) -> str:
        return f"AuditChain(len={len(self)})"


def _validate_payload(payload: Any) -> None:
    """Проверяет JSON-совместимость payload до передачи в Rust.

    Проверки:
    - int вне диапазона i64 -> ValueError
    - не-str ключ в dict -> ValueError
    - общая JSON-сериализуемость -> ValueError
    """
    import json

    def check(node: Any, path: str = "$") -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, int) and not (-2**63 <= node < 2**63):
            raise ValueError(
                f"integer out of i64 range at {path}: {node}. "
                f"Consider using str for large identifiers."
            )
        if isinstance(node, dict):
            for k, v in node.items():
                if not isinstance(k, str):
                    raise ValueError(
                        f"payload key must be str at {path}: {k!r}"
                    )
                check(v, f"{path}.{k}")
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                check(v, f"{path}[{i}]")

    check(payload)

    try:
        json.dumps(payload)
    except (TypeError, ValueError) as e:
        raise ValueError(f"payload not JSON-serializable: {e}") from e
