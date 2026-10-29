"""Append-only Audit Chain с hash-chain (чистый Python).

Каждая запись содержит sha256 от {seq, prev_hash, timestamp, event, payload}.
Изменение любой записи ломает verify() — tamper-evident.

Замена Rust-версии (PyO3) на Python. API 1:1:
- AuditChain.append(event, payload) -> AuditEntry
- AuditChain.verify() -> bool
- AuditChain.entries -> list[AuditEntry]
- len(chain), chain[i]

timestamp — Unix millis (u64). AuditEntry.timestamp — ISO-строка,
timestamp_millis — raw.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


GENESIS_HASH = "0" * 64


def _now_millis() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


@dataclass
class AuditEntry:
    """Одна запись в цепочке."""
    seq: int
    prev_hash: str
    timestamp_millis: int
    event: str
    _payload: Any          # хранится как dict/list/scalar
    entry_hash: str

    @property
    def timestamp(self) -> str:
        """ISO 8601 (UTC) из Unix millis."""
        dt = datetime.fromtimestamp(self.timestamp_millis / 1000, tz=timezone.utc)
        return dt.isoformat()

    @property
    def payload(self) -> Any:
        """Копия payload — мутация не влияет на цепочку."""
        return json.loads(json.dumps(self._payload))

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
        self._entries: list[AuditEntry] = []
        self._last_hash: str = GENESIS_HASH

    def append(self, event: str, payload: Any) -> AuditEntry:
        """Добавляет запись. payload — JSON-сериализуемый объект."""
        _validate_payload(payload)
        seq = len(self._entries) + 1
        ts = _now_millis()
        entry = AuditEntry(
            seq=seq,
            prev_hash=self._last_hash,
            timestamp_millis=ts,
            event=event,
            _payload=json.loads(json.dumps(payload)),  # копия
            entry_hash="",
        )
        entry.entry_hash = _compute_hash(entry)
        self._last_hash = entry.entry_hash
        self._entries.append(entry)
        return entry

    def append_batch(self, events: list[tuple[str, Any]]) -> list[AuditEntry]:
        """Добавляет пачку записей за один вызов."""
        return [self.append(e, p) for e, p in events]

    def verify(self) -> bool:
        """Проверяет целостность цепочки."""
        prev = GENESIS_HASH
        for e in self._entries:
            if e.prev_hash != prev:
                return False
            if _compute_hash(e) != e.entry_hash:
                return False
            prev = e.entry_hash
        return True

    @property
    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        return f"AuditChain(len={len(self)})"


def _compute_hash(entry: AuditEntry) -> str:
    canonical = {
        "seq": entry.seq,
        "prev_hash": entry.prev_hash,
        "timestamp": entry.timestamp_millis,
        "event": entry.event,
        "payload": entry._payload,
    }
    body = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _validate_payload(payload: Any) -> None:
    """Проверяет JSON-совместимость payload."""
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
                    raise ValueError(f"payload key must be str at {path}: {k!r}")
                check(v, f"{path}.{k}")
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                check(v, f"{path}[{i}]")

    check(payload)
    try:
        json.dumps(payload)
    except (TypeError, ValueError) as e:
        raise ValueError(f"payload not JSON-serializable: {e}") from e
