"""Property-based тесты через Hypothesis.

Проверяют инварианты, а не конкретные примеры.
Каждый тест генерирует 100+ случайных входов и проверяет, что свойство держится.

Запуск:
    pytest test_properties.py -v
    pytest test_properties.py -v -p no:randomly  # без случайного порядка
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st, HealthCheck

from agentic_guardrails.cost_cb import CostBreaker, Budget, CostState
from agentic_guardrails.failure_cb import FailureBreaker, CBState
from agentic_guardrails.audit import AuditChain


# ============================================================================
# CostBreaker — инварианты
# ============================================================================

class TestCostBreakerProperties:
    """Инварианты счётчика бюджета."""

    @given(st.lists(
        st.integers(min_value=0, max_value=10_000),
        min_size=1,
        max_size=50,
    ))
    @settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
    def test_sum_of_charges_equals_task_used(self, tokens):
        """Сумма всех charge(t) == task_used после N вызовов."""
        cb = CostBreaker(
            per_task=Budget(limit=10**12),
            per_session=Budget(limit=10**12),
            per_horizon=Budget(limit=10**12),
        )
        for t in tokens:
            cb.charge(t)
        assert cb.task_used == sum(tokens)
        assert cb.session_used == sum(tokens)

    @given(st.integers(min_value=0, max_value=10_000))
    @settings(max_examples=200)
    def test_charge_batch_equals_sequential(self, total_tokens):
        """charge_batch([t]) == charge(t)."""
        cb1 = CostBreaker(
            per_task=Budget(limit=10**12),
            per_session=Budget(limit=10**12),
            per_horizon=Budget(limit=10**12),
        )
        cb2 = CostBreaker(
            per_task=Budget(limit=10**12),
            per_session=Budget(limit=10**12),
            per_horizon=Budget(limit=10**12),
        )
        s1 = cb1.charge(total_tokens)
        s2 = cb2.charge_batch([total_tokens])
        assert s1 == s2
        assert cb1.task_used == cb2.task_used

    @given(st.integers(min_value=1, max_value=10**6))
    @settings(max_examples=100)
    def test_hard_state_is_terminal(self, limit):
        """После HARD — состояние не вернётся к OK без reset."""
        cb = CostBreaker(
            per_task=Budget(limit=limit),
            per_session=Budget(limit=limit),
            per_horizon=Budget(limit=limit),
        )
        cb.charge(limit)  # точно HARD
        assert cb.charge(0) == CostState.HARD
        cb.reset_task()
        # После reset_task task сброшен, но session и horizon — нет
        # session_used == limit -> HARD сохраняется
        assert cb.charge(0) == CostState.HARD


# ============================================================================
# FailureBreaker — инварианты
# ============================================================================

class TestFailureBreakerProperties:
    """Инварианты автомата состояний."""

    @given(
        threshold=st.integers(min_value=1, max_value=20),
        failures=st.integers(min_value=0, max_value=30),
    )
    @settings(max_examples=200)
    def test_opens_after_threshold_failures(self, threshold, failures):
        """OPEN ⇔ failures >= threshold."""
        cb = FailureBreaker(
            failure_threshold=threshold,
            recovery_timeout=9999.0,  # не истекает
        )
        for _ in range(failures):
            cb.on_failure()

        if failures >= threshold:
            assert cb.state is CBState.OPEN
        else:
            assert cb.state is CBState.CLOSED

    @given(threshold=st.integers(min_value=1, max_value=10))
    @settings(max_examples=50)
    def test_on_success_resets_state(self, threshold):
        """on_success сбрасывает счётчик и возвращает CLOSED."""
        cb = FailureBreaker(failure_threshold=threshold, recovery_timeout=9999.0)
        for _ in range(threshold):
            cb.on_failure()
        assert cb.state is CBState.OPEN

        cb.on_success()
        assert cb.state is CBState.CLOSED
        assert cb.failures == 0


# ============================================================================
# AuditChain — инварианты
# ============================================================================

class TestAuditChainProperties:
    """Инварианты журнала с цепочкой хешей."""

    @given(st.lists(
        st.tuples(
            st.text(min_size=1, max_size=20),
            st.dictionaries(
                st.text(min_size=1, max_size=10),
                st.one_of(st.integers(min_value=-2**63, max_value=2**63-1), st.text(max_size=20), st.booleans()),
                max_size=5,
            ),
        ),
        min_size=1,
        max_size=20,
    ))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_verify_always_true_after_append(self, events):
        """verify() == True после любого набора append."""
        chain = AuditChain()
        for event, payload in events:
            chain.append(event, payload)
        assert chain.verify() is True

    @given(st.lists(
        st.tuples(st.text(min_size=1, max_size=10), st.integers(min_value=-2**63, max_value=2**63-1)),
        min_size=1,
        max_size=10,
    ))
    @settings(max_examples=50)
    def test_len_equals_number_of_appends(self, events):
        """len(chain) == количество append."""
        chain = AuditChain()
        for event, payload in events:
            chain.append(event, payload)
        assert len(chain) == len(events)

    @given(st.text(min_size=1, max_size=10), st.integers(min_value=-2**63, max_value=2**63-1))
    @settings(max_examples=50)
    def test_seq_is_monotonic(self, event, payload):
        """seq увеличивается на 1 при каждом append."""
        chain = AuditChain()
        chain.append(event, payload)
        chain.append(event, payload)
        chain.append(event, payload)
        entries = chain.entries
        assert entries[0].seq == 1
        assert entries[1].seq == 2
        assert entries[2].seq == 3

    @given(st.text(min_size=1, max_size=10), st.integers(min_value=-2**63, max_value=2**63-1))
    @settings(max_examples=50)
    def test_payload_is_isolated(self, event, payload):
        """Мутация payload в Python не влияет на Rust-цепочку."""
        chain = AuditChain()
        chain.append(event, {"value": payload})
        # Мутируем копию
        chain.entries[0].payload["value"] = -999999
        # Rust-цепочка не изменилась
        assert chain.entries[0].payload["value"] == payload
        assert chain.verify() is True
