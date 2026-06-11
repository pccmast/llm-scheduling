"""Module 8: Circuit Breaker 测试 — 规格书 §4 Module 8."""

from __future__ import annotations

import time

import pytest

from src.dispatcher.circuit_breaker import CircuitBreakerConfig, CircuitBreakerRegistry, InstanceCircuitBreaker


@pytest.fixture
def breaker() -> InstanceCircuitBreaker:
    return InstanceCircuitBreaker("test-instance")


class TestInitialState:
    def test_initial_state_is_closed(self, breaker: InstanceCircuitBreaker) -> None:
        assert breaker.state == "closed"
        assert breaker.failure_count == 0
        assert breaker.allow_request() is True


class TestClosedToOpen:
    def test_open_after_threshold_failures(self) -> None:
        config = CircuitBreakerConfig(failure_threshold=3)
        cb = InstanceCircuitBreaker("inst-1", config)

        for _ in range(3):
            assert cb.state == "closed"
            cb.record_failure()

        assert cb.state == "open"

    def test_not_open_before_threshold(self) -> None:
        config = CircuitBreakerConfig(failure_threshold=3)
        cb = InstanceCircuitBreaker("inst-1", config)

        cb.record_failure()
        cb.record_failure()
        assert cb.state == "closed"

    def test_open_rejects_requests(self) -> None:
        config = CircuitBreakerConfig(failure_threshold=1, recovery_timeout=30.0)
        cb = InstanceCircuitBreaker("inst-1", config)
        cb.record_failure()
        assert cb.state == "open"
        assert cb.allow_request() is False


class TestOpenToHalfOpen:
    def test_half_open_after_recovery_timeout(self) -> None:
        config = CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0.01)
        cb = InstanceCircuitBreaker("inst-1", config)
        cb.record_failure()
        assert cb.state == "open"

        time.sleep(0.02)  # Wait for recovery timeout
        result = cb.allow_request()
        assert result is True
        assert cb.state == "half_open"

    def test_state_property_does_not_trigger_transition(self) -> None:
        """state 属性不触发惰性转换，只读。"""
        config = CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0.01)
        cb = InstanceCircuitBreaker("inst-1", config)
        cb.record_failure()
        time.sleep(0.02)
        # state property should still show OPEN until allow_request() is called
        assert cb.state == "open"
        # After allow_request(), state should transition
        cb.allow_request()
        assert cb.state == "half_open"


class TestHalfOpenToClosed:
    def test_half_open_success_returns_to_closed(self) -> None:
        config = CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0.01, half_open_max_calls=1)
        cb = InstanceCircuitBreaker("inst-1", config)
        cb.record_failure()
        time.sleep(0.02)
        cb.allow_request()

        assert cb.state == "half_open"
        cb.record_success()
        assert cb.state == "closed"
        assert cb.failure_count == 0

    def test_half_open_multiple_successes_needed(self) -> None:
        config = CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0.01, half_open_max_calls=3)
        cb = InstanceCircuitBreaker("inst-1", config)
        cb.record_failure()
        time.sleep(0.02)

        # Three probe requests and three successes
        cb.allow_request()
        cb.allow_request()
        cb.allow_request()
        assert cb.state == "half_open"

        cb.record_success()
        assert cb.state == "half_open"  # not enough yet
        cb.record_success()
        assert cb.state == "half_open"  # still not enough
        cb.record_success()
        assert cb.state == "closed"


class TestHalfOpenToOpen:
    def test_half_open_failure_returns_to_open(self) -> None:
        config = CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0.01)
        cb = InstanceCircuitBreaker("inst-1", config)
        cb.record_failure()
        time.sleep(0.02)
        cb.allow_request()

        assert cb.state == "half_open"
        cb.record_failure()
        assert cb.state == "open"

    def test_half_open_failure_resets_opened_at(self) -> None:
        """HALF_OPEN 失败后 recovery_timeout 重新计时。"""
        config = CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0.02)
        cb = InstanceCircuitBreaker("inst-1", config)
        cb.record_failure()
        time.sleep(0.03)
        cb.allow_request()
        cb.record_failure()
        # Immediately after failure, still OPEN
        assert cb.state == "open"
        assert cb.allow_request() is False
        # Wait for recovery
        time.sleep(0.03)
        assert cb.allow_request() is True


class TestClosedSuccess:
    def test_success_resets_failure_count(self) -> None:
        config = CircuitBreakerConfig(failure_threshold=3)
        cb = InstanceCircuitBreaker("inst-1", config)
        cb.record_failure()
        cb.record_failure()
        assert cb.failure_count == 2
        cb.record_success()
        assert cb.failure_count == 0

    def test_success_in_closed_keeps_state(self) -> None:
        cb = InstanceCircuitBreaker("inst-1")
        cb.record_success()
        assert cb.state == "closed"


class TestReset:
    def test_reset_from_open(self) -> None:
        config = CircuitBreakerConfig(failure_threshold=1)
        cb = InstanceCircuitBreaker("inst-1", config)
        cb.record_failure()
        assert cb.state == "open"

        cb.reset()
        assert cb.state == "closed"
        assert cb.failure_count == 0

    def test_reset_from_half_open(self) -> None:
        config = CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0.01)
        cb = InstanceCircuitBreaker("inst-1", config)
        cb.record_failure()
        time.sleep(0.02)
        cb.allow_request()
        assert cb.state == "half_open"

        cb.reset()
        assert cb.state == "closed"


class TestRegistry:
    def test_get_or_create_returns_same_instance(self) -> None:
        registry = CircuitBreakerRegistry()
        cb1 = registry.get_or_create("inst-1")
        cb2 = registry.get_or_create("inst-1")
        assert cb1 is cb2

    def test_get_all_states(self) -> None:
        registry = CircuitBreakerRegistry()
        registry.get_or_create("inst-1")
        cb2 = registry.get_or_create("inst-2")
        cb2.record_failure()
        cb2.record_failure()
        cb2.record_failure()
        cb2.record_failure()
        cb2.record_failure()

        states = registry.get_all_states()
        assert states["inst-1"] == "closed"
        assert states["inst-2"] == "open"

    def test_remove(self) -> None:
        registry = CircuitBreakerRegistry()
        registry.get_or_create("inst-1")
        registry.remove("inst-1")
        assert registry.get_all_states() == {}
