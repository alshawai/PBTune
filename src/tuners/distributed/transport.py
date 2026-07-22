# Copyright (C) 2026 Ibrahim Al-Shawa and PBTune contributors
# Licensed under the GNU General Public License v3.0
# See LICENSE file for details

"""
RPC Transport
=============

Thin, dependency-free HTTP/JSON transport shared by the coordinator (client)
and the device agent (server). Uses only the Python standard library
(``urllib``, ``http.server``, ``json``) in keeping with this repo's
minimal-dependency philosophy — the API surface between coordinator and agent
is small (seven endpoints on a trusted network), so a full web framework would
be overkill.

Client: :class:`AgentClient`.
Server helpers: :func:`read_json_body`, :func:`write_json` (used by
``device_agent``'s ``BaseHTTPRequestHandler``).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from src.tuners.distributed.agent_api import ErrorResponse


class AgentRPCError(RuntimeError):
    """Raised when an agent RPC fails (transport error or non-2xx response)."""

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        error: Optional[ErrorResponse] = None,
        url: Optional[str] = None,
    ):
        super().__init__(message)
        self.status = status
        self.error = error
        self.url = url


class AgentClient:
    """Minimal JSON-over-HTTP client for a single device agent.

    Parameters
    ----------
    base_url:
        e.g. ``http://10.0.0.11:8770``.
    default_timeout:
        Fallback per-request timeout (seconds) when a call doesn't specify one.
    """

    def __init__(self, base_url: str, default_timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.default_timeout = default_timeout

    def _request(
        self,
        method: str,
        route: str,
        payload: Optional[Dict[str, Any]],
        timeout: Optional[float],
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{route}"
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(
                req, timeout=timeout or self.default_timeout
            ) as resp:
                raw = resp.read().decode("utf-8") or "{}"
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            body = {}
            try:
                body = json.loads(exc.read().decode("utf-8") or "{}")
            except (ValueError, OSError):
                pass
            err = ErrorResponse.from_dict(body) if body else None
            detail = err.error if err else exc.reason
            raise AgentRPCError(
                f"{method} {url} -> HTTP {exc.code}: {detail}",
                status=exc.code,
                error=err,
                url=url,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise AgentRPCError(
                f"{method} {url} -> transport error: {reason}", url=url
            ) from exc
        except ValueError as exc:  # malformed JSON in a 2xx body
            raise AgentRPCError(
                f"{method} {url} -> invalid JSON response: {exc}", url=url
            ) from exc

    def get(self, route: str, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        return self._request("GET", route, None, timeout)

    def post(
        self,
        route: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        return self._request("POST", route, payload or {}, timeout)


# --------------------------------------------------------------------------- #
# Server-side helpers (used by device_agent's request handler)
# --------------------------------------------------------------------------- #
def read_json_body(handler) -> Dict[str, Any]:
    """Read and parse a JSON request body from a BaseHTTPRequestHandler.

    Returns an empty dict when there is no body. Raises ``ValueError`` on
    malformed JSON so the handler can reply 400.
    """
    length = int(handler.headers.get("Content-Length", 0) or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length).decode("utf-8")
    if not raw.strip():
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("request body must be a JSON object")
    return parsed


def write_json(handler, status: int, obj: Dict[str, Any]) -> None:
    """Serialise ``obj`` as JSON and write it as the HTTP response."""
    body = json.dumps(obj).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
