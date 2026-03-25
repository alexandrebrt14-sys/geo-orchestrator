"""
Circuit Breaker implementation for LLM providers.
Prevents cascading failures and provides automatic recovery.
"""

import time
import threading
from enum import Enum
from typing import Dict, Optional, Callable, Any
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "CLOSED"  # Normal operation, requests allowed
    OPEN = "OPEN"      # Circuit broken, requests blocked
    HALF_OPEN = "HALF_OPEN"  # Testing if service recovered


@dataclass
class CircuitStats:
    """Statistics for circuit breaker monitoring."""
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    total_failures: int = 0
    total_successes: int = 0
    last_state_change: float = field(default_factory=time.time)


class CircuitBreakerError(Exception):
    """Raised when circuit is open and request is blocked."""
    pass


class CircuitBreaker:
    """
    Circuit breaker implementation with configurable thresholds.
    Thread-safe for concurrent usage.
    """
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout: float = 60.0,
        expected_exception: type = Exception
    ):
        """
        Initialize circuit breaker.
        
        Args:
            name: Identifier for this circuit breaker
            failure_threshold: Consecutive failures before opening circuit
            success_threshold: Consecutive successes needed to close circuit
            timeout: Seconds to wait before trying half-open state
            expected_exception: Exception types that count as failures
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout = timeout
        self.expected_exception = expected_exception
        
        self._state = CircuitState.CLOSED
        self._stats = CircuitStats()
        self._lock = threading.RLock()
        
        logger.info(f"Circuit breaker '{name}' initialized: "
                   f"failure_threshold={failure_threshold}, "
                   f"timeout={timeout}s")
    
    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        with self._lock:
            return self._state
    
    @property
    def stats(self) -> Dict[str, Any]:
        """Get circuit breaker statistics."""
        with self._lock:
            return {
                "state": self._state.value,
                "consecutive_failures": self._stats.consecutive_failures,
                "consecutive_successes": self._stats.consecutive_successes,
                "total_failures": self._stats.total_failures,
                "total_successes": self._stats.total_successes,
                "last_failure_time": self._stats.last_failure_time,
                "last_success_time": self._stats.last_success_time,
                "time_since_last_failure": (
                    time.time() - self._stats.last_failure_time
                    if self._stats.last_failure_time else None
                )
            }
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function through circuit breaker.
        
        Args:
            func: Function to execute
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func
            
        Returns:
            Result from func
            
        Raises:
            CircuitBreakerError: If circuit is open
            Exception: If func raises an exception
        """
        with self._lock:
            if self._state == CircuitState.OPEN:
                # Check if timeout has passed
                if self._should_attempt_reset():
                    self._transition_to_half_open()
                else:
                    raise CircuitBreakerError(
                        f"Circuit breaker '{self.name}' is OPEN. "
                        f"Time until retry: {self._time_until_retry():.1f}s"
                    )
        
        # Execute the function
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception as e:
            self._on_failure()
            raise e
    
    async def call_async(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute async function through circuit breaker.
        
        Args:
            func: Async function to execute
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func
            
        Returns:
            Result from func
            
        Raises:
            CircuitBreakerError: If circuit is open
            Exception: If func raises an exception
        """
        with self._lock:
            if self._state == CircuitState.OPEN:
                # Check if timeout has passed
                if self._should_attempt_reset():
                    self._transition_to_half_open()
                else:
                    raise CircuitBreakerError(
                        f"Circuit breaker '{self.name}' is OPEN. "
                        f"Time until retry: {self._time_until_retry():.1f}s"
                    )
        
        # Execute the async function
        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception as e:
            self._on_failure()
            raise e
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        return (
            self._stats.last_failure_time is not None and
            time.time() - self._stats.last_failure_time >= self.timeout
        )
    
    def _time_until_retry(self) -> float:
        """Calculate seconds until retry is allowed."""
        if self._stats.last_failure_time is None:
            return 0.0
        elapsed = time.time() - self._stats.last_failure_time
        return max(0.0, self.timeout - elapsed)
    
    def _on_success(self):
        """Handle successful execution."""
        with self._lock:
            self._stats.consecutive_failures = 0
            self._stats.consecutive_successes += 1
            self._stats.total_successes += 1
            self._stats.last_success_time = time.time()
            
            if self._state == CircuitState.HALF_OPEN:
                if self._stats.consecutive_successes >= self.success_threshold:
                    self._transition_to_closed()
                    
            logger.debug(f"Circuit breaker '{self.name}': Success recorded. "
                        f"State: {self._state.value}")
    
    def _on_failure(self):
        """Handle failed execution."""
        with self._lock:
            self._stats.consecutive_failures += 1
            self._stats.consecutive_successes = 0
            self._stats.total_failures += 1
            self._stats.last_failure_time = time.time()
            
            if self._state == CircuitState.HALF_OPEN:
                # Single failure in half-open state reopens the circuit
                self._transition_to_open()
            elif (self._state == CircuitState.CLOSED and 
                  self._stats.consecutive_failures >= self.failure_threshold):
                self._transition_to_open()
                
            logger.warning(f"Circuit breaker '{self.name}': Failure recorded. "
                          f"Consecutive failures: {self._stats.consecutive_failures}. "
                          f"State: {self._state.value}")
    
    def _transition_to_open(self):
        """Transition to OPEN state."""
        self._state = CircuitState.OPEN
        self._stats.last_state_change = time.time()
        logger.error(f"Circuit breaker '{self.name}' transitioned to OPEN")
    
    def _transition_to_closed(self):
        """Transition to CLOSED state."""
        self._state = CircuitState.CLOSED
        self._stats.consecutive_failures = 0
        self._stats.consecutive_successes = 0
        self._stats.last_state_change = time.time()
        logger.info(f"Circuit breaker '{self.name}' transitioned to CLOSED")
    
    def _transition_to_half_open(self):
        """Transition to HALF_OPEN state."""
        self._state = CircuitState.HALF_OPEN
        self._stats.consecutive_successes = 0
        self._stats.last_state_change = time.time()
        logger.info(f"Circuit breaker '{self.name}' transitioned to HALF_OPEN")
    
    def reset(self):
        """Manually reset circuit breaker to closed state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._stats = CircuitStats()
            logger.info(f"Circuit breaker '{self.name}' manually reset")


class CircuitBreakerRegistry:
    """
    Registry for managing multiple circuit breakers.
    Provides centralized access to all breakers.
    """
    
    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()
    
    def get_or_create(
        self,
        name: str,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout: float = 60.0,
        expected_exception: type = Exception
    ) -> CircuitBreaker:
        """
        Get existing circuit breaker or create new one.
        
        Args:
            name: Circuit breaker identifier
            failure_threshold: Consecutive failures before opening
            success_threshold: Successes needed to close
            timeout: Seconds before attempting half-open
            expected_exception: Exception types that count as failures
            
        Returns:
            CircuitBreaker instance
        """
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(
                    name=name,
                    failure_threshold=failure_threshold,
                    success_threshold=success_threshold,
                    timeout=timeout,
                    expected_exception=expected_exception
                )
            return self._breakers[name]
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all circuit breakers."""
        with self._lock:
            return {
                name: breaker.stats
                for name, breaker in self._breakers.items()
            }
    
    def reset_all(self):
        """Reset all circuit breakers."""
        with self._lock:
            for breaker in self._breakers.values():
                breaker.reset()


# Global registry instance
circuit_breaker_registry = CircuitBreakerRegistry()