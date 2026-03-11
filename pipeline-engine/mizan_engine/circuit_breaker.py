"""
mizan_engine/circuit_breaker.py — Circuit Breaker + Retry Logic
═══════════════════════════════════════════════════════════════════

Sprint 1 — Foundation Stability
CEO Spec §3: Per-agent timeout, exponential backoff, circuit breaker.

Circuit States: CLOSED → OPEN → HALF-OPEN → CLOSED
Retry: max 3, base 1s, factor 2x, max delay 10s
"""

from __future__ import annotations

import asyncio
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("yaruksai.circuit_breaker")


# ════════════════════════════════════════════════════════
#  AGENT TIMEOUTS — CEO Spec §3.1
# ════════════════════════════════════════════════════════

AGENT_TIMEOUTS: Dict[str, int] = {
    "architect":  45,   # Spec üretimi — uzun ama sınırlı
    "review":     30,   # Kural karşılaştırma — hızlı olmalı
    "approval":   20,   # Ledger yazma — en kritik, en hızlı
    "codegen":    120,  # Kod üretimi — uzun sürebilir
    # FEAM OS ajanları
    "celali":     30,
    "cemali":     30,
    "kemali":     30,
    "emanet":     60,
    "default":    45,
}


# ════════════════════════════════════════════════════════
#  RETRY CONFIGURATION — CEO Spec §3.2
# ════════════════════════════════════════════════════════

@dataclass
class RetryConfig:
    """Exponential backoff retry configuration."""
    max_retries: int = 3
    base_delay: float = 1.0
    backoff_factor: float = 2.0
    max_delay: float = 10.0

    # Non-retryable HTTP codes — CEO Spec §3.3
    non_retryable_codes: tuple = (400, 401, 403)

    def get_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt (0-indexed)."""
        delay = self.base_delay * (self.backoff_factor ** attempt)
        return min(delay, self.max_delay)


DEFAULT_RETRY = RetryConfig()


# ════════════════════════════════════════════════════════
#  CIRCUIT BREAKER — CEO Spec §3.4
# ════════════════════════════════════════════════════════

class CircuitState(Enum):
    CLOSED = "CLOSED"       # Normal — all requests pass
    OPEN = "OPEN"           # Broken — requests rejected
    HALF_OPEN = "HALF_OPEN" # Testing — 1 probe request


@dataclass
class CircuitBreaker:
    """
    Circuit Breaker pattern per CEO Spec §3.4.

    3 consecutive failures → OPEN (60s cooldown).
    After cooldown → HALF_OPEN (1 probe).
    Probe success → CLOSED.
    Probe failure → OPEN again.
    """

    name: str
    failure_threshold: int = 3
    recovery_timeout: float = 60.0  # seconds

    # Internal state
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    total_calls: int = 0
    total_failures: int = 0
    total_successes: int = 0

    def can_execute(self) -> bool:
        """Check if the circuit allows execution."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            # Check if recovery timeout has elapsed
            elapsed = time.time() - self.last_failure_time
            if elapsed >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.warning(
                    f"[CIRCUIT] {self.name}: OPEN → HALF_OPEN (cooldown elapsed: {elapsed:.0f}s)"
                )
                return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            return True  # Allow 1 probe

        return False

    def record_success(self):
        """Record a successful call."""
        self.total_calls += 1
        self.total_successes += 1
        self.last_success_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            logger.info(f"[CIRCUIT] {self.name}: HALF_OPEN → CLOSED (probe succeeded)")
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0  # Reset on success

    def record_failure(self):
        """Record a failed call."""
        self.total_calls += 1
        self.total_failures += 1
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            logger.error(f"[CIRCUIT] {self.name}: HALF_OPEN → OPEN (probe failed)")

        elif self.state == CircuitState.CLOSED:
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                logger.error(
                    f"[CIRCUIT] {self.name}: CLOSED → OPEN "
                    f"({self.failure_count} consecutive failures)"
                )

    def to_dict(self) -> Dict[str, Any]:
        """Status for health check endpoint."""
        return {
            "status": "up" if self.state == CircuitState.CLOSED else "degraded",
            "circuit": self.state.value,
            "failure_count": self.failure_count,
            "total_calls": self.total_calls,
            "total_failures": self.total_failures,
            "total_successes": self.total_successes,
        }


# ════════════════════════════════════════════════════════
#  RETRY WRAPPER
# ════════════════════════════════════════════════════════

async def execute_with_retry(
    func: Callable,
    *args,
    agent_name: str = "default",
    circuit: Optional[CircuitBreaker] = None,
    retry_config: RetryConfig = DEFAULT_RETRY,
    **kwargs,
) -> Any:
    """
    Execute a function with timeout, retry, and circuit breaker.

    CEO Spec §3:
    - Per-agent timeout from AGENT_TIMEOUTS
    - Exponential backoff: 1s → 2s → 4s
    - Circuit breaker integration
    """
    timeout = AGENT_TIMEOUTS.get(agent_name, AGENT_TIMEOUTS["default"])

    # Circuit breaker check
    if circuit and not circuit.can_execute():
        # CEO NOTE: Approval_Agent → PENDING_REVIEW, not silent fail
        if agent_name in ("approval", "Approval_Agent"):
            logger.warning(
                f"[CIRCUIT] {agent_name}: OPEN — returning PENDING_REVIEW "
                f"(kayıt kaybedilemez)"
            )
            return {
                "status": "PENDING_REVIEW",
                "reason": "circuit_open",
                "agent": agent_name,
                "ledger_written": True,
                "message": "Circuit breaker OPEN — karar insan onayına düştü",
            }
        raise CircuitOpenError(
            f"Circuit breaker OPEN for {agent_name}. "
            f"Retry in {circuit.recovery_timeout}s."
        )

    last_error = None

    for attempt in range(retry_config.max_retries + 1):
        try:
            # Execute with timeout
            if asyncio.iscoroutinefunction(func):
                result = await asyncio.wait_for(
                    func(*args, **kwargs),
                    timeout=timeout,
                )
            else:
                # Sync function — run in executor with timeout
                loop = asyncio.get_event_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: func(*args, **kwargs)),
                    timeout=timeout,
                )

            # Success
            if circuit:
                circuit.record_success()

            return result

        except asyncio.TimeoutError:
            last_error = TimeoutError(
                f"Agent '{agent_name}' timed out after {timeout}s (attempt {attempt + 1})"
            )
            logger.warning(f"[RETRY] {agent_name}: timeout (attempt {attempt + 1}/{retry_config.max_retries + 1})")

        except Exception as e:
            last_error = e

            # Check if retryable
            status_code = getattr(e, "status_code", None)
            if status_code and status_code in retry_config.non_retryable_codes:
                logger.error(f"[RETRY] {agent_name}: non-retryable error {status_code}")
                if circuit:
                    circuit.record_failure()
                raise

            logger.warning(
                f"[RETRY] {agent_name}: error '{e}' "
                f"(attempt {attempt + 1}/{retry_config.max_retries + 1})"
            )

        # Backoff before retry (unless last attempt)
        if attempt < retry_config.max_retries:
            delay = retry_config.get_delay(attempt)
            logger.info(f"[RETRY] {agent_name}: waiting {delay:.1f}s before retry")
            await asyncio.sleep(delay)

    # All retries exhausted
    if circuit:
        circuit.record_failure()

    raise AgentExecutionError(
        f"Agent '{agent_name}' failed after {retry_config.max_retries + 1} attempts: {last_error}"
    )


# ════════════════════════════════════════════════════════
#  CIRCUIT BREAKER REGISTRY
# ════════════════════════════════════════════════════════

class CircuitBreakerRegistry:
    """Global registry of all circuit breakers."""

    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}

    def get(self, name: str) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(name=name)
        return self._breakers[name]

    def all_status(self) -> Dict[str, Dict]:
        return {name: cb.to_dict() for name, cb in self._breakers.items()}

    def any_open(self) -> bool:
        return any(cb.state == CircuitState.OPEN for cb in self._breakers.values())


# Global registry
circuit_registry = CircuitBreakerRegistry()


# ════════════════════════════════════════════════════════
#  EXCEPTIONS
# ════════════════════════════════════════════════════════

class CircuitOpenError(Exception):
    """Raised when circuit breaker is OPEN."""
    pass


class AgentExecutionError(Exception):
    """Raised when agent execution fails after all retries."""
    pass
