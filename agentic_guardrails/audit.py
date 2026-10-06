"""Append-only Audit Chain с hash-chain — обёртка над Rust-ядром.

API 1:1 с прежней Python-версией. timestamp снаружи — ISO-строка,
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
        """Добавляет запись. payload — dict или любой JSON-сериализуемый объект."""
        return AuditEntry(self._inner.append(event, payload))

    def verify(self) -> bool:
        """Проверяет целостность всей цепочки."""
        return self._inner.verify()

    @property
    def entries(self) -> list[AuditEntry]:
        return [AuditEntry(e) for e in self._inner.entries()]

    def __len__(self) -> int:
        return len(self._inner)

    def __repr__(self) -> str:
        return f"AuditChain(len={len(self)})"
