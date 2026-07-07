"""Shared HTTP transport tuning for Alpaca SDK clients.

Alpaca's edge closes idle keep-alive connections; the next request that reuses
a pooled connection then fails immediately with
``RemoteDisconnected('Remote end closed connection without response')``.
Mounting a urllib3 retry policy makes the underlying session transparently
retry on a fresh connection for idempotent HTTP methods (GET/DELETE/PUT/...).
POST is intentionally left out of urllib3's default allowed methods, so a
dropped connection during order submission is never silently retried into a
duplicate order.
"""
from __future__ import annotations

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_RETRY = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=0.5,
    status_forcelist=(429, 500, 502, 503, 504),
)


def harden_alpaca_client(client) -> None:
    """Mount a connection-retry adapter on an alpaca-py REST client's session."""
    session = getattr(client, "_session", None)
    if session is None:
        return
    adapter = HTTPAdapter(max_retries=_RETRY)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
