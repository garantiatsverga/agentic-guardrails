"""Evidence Bundle — контракт завершения задачи.

Паспорт: что сделано, какие решения приняты, какие evals прошли.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any
import json


@dataclass
class EvidenceBundle:
    task_id: str
    agent_id: str
    outcome: str                       # "success" | "failure" | "escalated"
    decisions: list[dict[str, Any]] = field(default_factory=list)
    evals: dict[str, float] = field(default_factory=dict)
    token_cost: int = 0
    duration_ms: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def add_decision(self, action: str, decision: str, reason: str = "") -> None:
        self.decisions.append({
            "action": action, "decision": decision, "reason": reason,
        })

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)
