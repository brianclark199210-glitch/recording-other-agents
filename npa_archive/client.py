"""Read-only client for the public Never Play Alone API."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_BASE = "https://api.neverplayalone.ai"
USER_AGENT = "npa-archive/1.0"


class ApiError(RuntimeError):
    def __init__(self, status: int, path: str, body: str) -> None:
        super().__init__(f"{status} {path}: {body[:240]}")
        self.status = status
        self.path = path


class NpaClient:
    def __init__(self, base_url: str = API_BASE, timeout: float = 90) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get_json(self, path: str, retries: int = 4) -> Any:
        url = self.base_url + path
        delay = 1.0
        last_error: Exception | None = None
        for attempt in range(retries):
            request = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                last_error = ApiError(exc.code, path, body)
                if exc.code not in {429, 500, 502, 503, 504} or attempt == retries - 1:
                    raise last_error from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt == retries - 1:
                    raise RuntimeError(f"request failed for {path}: {exc}") from exc
            time.sleep(delay)
            delay = min(delay * 2, 12)
        else:
            raise RuntimeError(f"request failed for {path}: {last_error}")
        if isinstance(payload, dict) and "data" in payload and payload.get("success") is True:
            return payload["data"]
        return payload

    def download(self, url: str, dest_tmp: Any, retries: int = 4) -> None:
        """Stream a presigned artifact URL into a binary file object."""
        delay = 1.0
        for attempt in range(retries):
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=max(self.timeout, 300)) as response:
                    while True:
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            return
                        dest_tmp.write(chunk)
                return
            except urllib.error.HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == retries - 1:
                    body = exc.read().decode("utf-8", errors="replace")
                    raise ApiError(exc.code, url.split("?", 1)[0], body) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == retries - 1:
                    raise RuntimeError(f"download failed: {exc}") from exc
            dest_tmp.seek(0)
            dest_tmp.truncate(0)
            time.sleep(delay)
            delay = min(delay * 2, 12)

    def current_round(self) -> dict[str, Any]:
        data = self.get_json("/api/v1/rounds/current")
        if not isinstance(data, dict):
            raise TypeError("current round response was not an object")
        return data

    def rounds(self, limit: int = 500) -> list[dict[str, Any]]:
        data = self.get_json(f"/api/v1/rounds?limit={int(limit)}")
        rows = data.get("rounds") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise TypeError("rounds response did not include a list")
        return [row for row in rows if isinstance(row, dict)]

    def overview(self) -> dict[str, Any]:
        data = self.get_json("/api/v1/overview/metrics")
        if not isinstance(data, dict):
            raise TypeError("overview response was not an object")
        return data

    def agent(self, miner_uid: int) -> dict[str, Any]:
        data = self.get_json(f"/api/v1/agents/{int(miner_uid)}")
        if not isinstance(data, dict):
            raise TypeError(f"agent {miner_uid} response was not an object")
        return data

    def agent_code(self, miner_uid: int) -> dict[str, Any]:
        data = self.get_json(f"/api/v1/agents/{int(miner_uid)}/code")
        if not isinstance(data, dict):
            raise TypeError(f"agent {miner_uid} code response was not an object")
        return data

    def agent_run(self, agent_run_id: str) -> dict[str, Any]:
        quoted = urllib.parse.quote(agent_run_id, safe=":")
        data = self.get_json(f"/api/v1/agent-runs/{quoted}")
        if not isinstance(data, dict):
            raise TypeError(f"run {agent_run_id} response was not an object")
        return data
