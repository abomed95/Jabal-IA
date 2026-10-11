import threading
import time
from collections import defaultdict, deque

from app.core.config import get_settings


class LoginThrottle:
    """Limite les échecs de connexion par numéro, en mémoire.

    Limite connue : l'état est propre à chaque instance. Avec 2 instances en production,
    l'attaquant dispose de 2 fois plus d'essais. À déplacer en base si cela devient un souci.
    """

    def __init__(self) -> None:
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        window = get_settings().login_window_seconds
        q = self._failures[key]
        while q and now - q[0] > window:
            q.popleft()
        return q

    def is_blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, time.monotonic())) >= get_settings().login_max_failures

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._prune(key, now).append(now)

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._failures.clear()
            else:
                self._failures.pop(key, None)


login_throttle = LoginThrottle()
