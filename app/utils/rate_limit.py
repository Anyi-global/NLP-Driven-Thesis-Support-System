"""Small in-memory sliding-window rate limiter.

Per-process only: with several worker processes each has its own counters, so the effective
limit is multiplied. For multi-worker deployments swap this for Flask-Limiter + Redis.
"""
import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self._hits, self._lock, self._clock = {}, threading.Lock(), clock

    def _prune(self, key, window):
        q, cutoff = self._hits.setdefault(key, deque()), self._clock() - window
        while q and q[0] <= cutoff:
            q.popleft()
        return q

    def is_limited(self, key, limit, window):
        with self._lock:
            return len(self._prune(key, window)) >= limit

    def retry_after(self, key, window):
        with self._lock:
            q = self._prune(key, window)
            return max(1, int(window - (self._clock() - q[0]))) if q else 0

    def hit(self, key, window):
        with self._lock:
            self._prune(key, window).append(self._clock())
            if len(self._hits) > 10000:  # bound memory
                for k in [k for k, v in self._hits.items() if not v][:5000]:
                    self._hits.pop(k, None)

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)


limiter = RateLimiter()


def rate_limited_response(name, ident, limit, window, config):
    """Return a 429 response if `ident` exceeded `limit` hits in `window` seconds, else None.
    Does NOT record a hit; call limiter.hit(key, window) for the events that should count."""
    if not config.get("RATE_LIMIT_ENABLED", True):
        return None
    key = "%s:%s" % (name, ident)
    if limiter.is_limited(key, limit, window):
        from flask import jsonify
        resp = jsonify({"error": "Too Many Requests", "message": "Too many attempts. Please wait and try again."})
        resp.status_code = 429
        resp.headers["Retry-After"] = str(limiter.retry_after(key, window))
        return resp
    return None
