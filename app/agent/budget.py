import threading

class IterationBudget:
    """
    A thread-safe iteration budget counter to prevent infinite agentic loops.
    """
    def __init__(self, limit: int = 20):
        self._limit = limit
        self._remaining = limit
        self._lock = threading.Lock()

    def consume(self, amount: int = 1) -> None:
        """
        Consumes a given amount from the budget.
        Raises ValueError if the budget is exceeded or exhausted.
        """
        with self._lock:
            if self._remaining < amount:
                raise ValueError(
                    f"Iteration budget exhausted: cannot consume {amount} with only {self._remaining} remaining."
                )
            self._remaining -= amount

    def refund(self, amount: int = 1) -> None:
        """
        Refunds a given amount to the budget.
        """
        with self._lock:
            self._remaining += amount

    @property
    def remaining(self) -> int:
        """
        Returns the remaining budget.
        """
        with self._lock:
            return self._remaining

    @property
    def limit(self) -> int:
        """
        Returns the original budget limit.
        """
        return self._limit
