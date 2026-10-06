"""Append-only Audit Chain — неизменяемый лог с hash-chain.

Каждая запись содержит хеш предыдущей -> tamper-evident.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
import json


@dataclass
class AuditEntry:
    seq: int
    prev_hash: str
    timestamp: str
    event: str
    payload: dict[str, Any]
    entry_hash: str = ""

    def compute_hash(self) -> str:
        body = json.dumps(
            {k: v for k, v in asdict(self).items() if k != "entry_hash"},
            sort_keys=True, ensure_ascii=False,
        )
        return sha256(body.encode()).hexdigest()


class AuditChain:
    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self._last_hash = "0" * 64

    def append(self, event: str, payload: dict[str, Any]) -> AuditEntry:
        entry = AuditEntry(
            seq=len(self._entries) + 1,
            prev_hash=self._last_hash,
            timestamp=datetime.now(timezone.utc).isoformat(),
            event=event,
            payload=payload,
        )
        entry.entry_hash = entry.compute_hash()
        self._last_hash = entry.entry_hash
        self._entries.append(entry)
        return entry

    def verify(self) -> bool:
        prev = "0" * 64
        for e in self._entries:
            if e.prev_hash != prev or e.compute_hash() != e.entry_hash:
                return False
            prev = e.entry_hash
        return True

    @property
    def entries(self) -> list[AuditEntry]:
        return list(self._entries)
