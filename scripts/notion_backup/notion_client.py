from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from typing import Any
from urllib import error, parse, request

LOGGER = logging.getLogger(__name__)

class NotionApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: dict[str, Any] | None = None,
        response_text: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body or {}
        self.response_text = response_text


class _RateLimiter:

    def __init__(self, requests_per_second: float, burst: int = 3) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        if burst < 1:
            raise ValueError("burst must be at least 1")
        self._rate = requests_per_second
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._lock = threading.Lock()
        self._last_refill = time.monotonic()

    def wait(self) -> float:
        total_sleep = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._last_refill = now
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return total_sleep
                deficit = 1.0 - self._tokens
                sleep_for = deficit / self._rate
            time.sleep(sleep_for)
            total_sleep += sleep_for


class NotionClient:

    BASE_URL = "https://api.notion.com/v1"

    def __init__(
        self,
        token: str | None = None,
        notion_version: str = "2022-06-28",
        timeout_seconds: int = 180,
        max_retries: int = 8,
        page_size: int = 100,
        retry_initial_sleep_seconds: float = 2.0,
        retry_max_sleep_seconds: float = 120.0,
        requests_per_second: float = 2.5,
        burst: int = 3,
    ) -> None:
        self.token = token or os.getenv("NOTION_TOKEN")
        if not self.token:
            raise ValueError("NOTION_TOKEN is required")
        self.notion_version = notion_version
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.page_size = min(max(page_size, 1), 100)
        self.retry_initial_sleep_seconds = retry_initial_sleep_seconds
        self.retry_max_sleep_seconds = retry_max_sleep_seconds
        self.requests_per_second = requests_per_second
        self.burst = burst
        self._rate_limiter = _RateLimiter(requests_per_second, burst=burst)
        self.total_requests = 0
        self.total_rate_limit_wait_seconds = 0.0
        self.total_retry_wait_seconds = 0.0

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        return self._request("GET", f"/pages/{page_id}")

    def retrieve_database(self, database_id: str) -> dict[str, Any]:
        return self._request("GET", f"/databases/{database_id}")

    def list_block_children(self, block_id: str) -> list[dict[str, Any]]:
        return list(self._paginate("GET", f"/blocks/{block_id}/children"))

    def query_database(self, database_id: str) -> list[dict[str, Any]]:
        return list(self._paginate("POST", f"/databases/{database_id}/query", body={}))

    def search_all(self) -> list[dict[str, Any]]:
        return list(
            self._paginate(
                "POST",
                "/search",
                body={"sort": {"direction": "ascending", "timestamp": "last_edited_time"}},
            )
        )

    def search_pages(self, query: str, page_size: int | None = None) -> list[dict[str, Any]]:
        return list(
            self._paginate(
                "POST",
                "/search",
                body={
                    "query": query,
                    "filter": {"property": "object", "value": "page"},
                    "sort": {"direction": "ascending", "timestamp": "last_edited_time"},
                },
                page_size=page_size,
            )
        )

    def _paginate(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        page_size: int | None = None,
    ) -> list[dict[str, Any]]:
        cursor: str | None = None
        results: list[dict[str, Any]] = []

        while True:
            request_body = dict(body or {})
            request_body["page_size"] = min(max(page_size or self.page_size, 1), 100)
            if cursor:
                request_body["start_cursor"] = cursor

            request_path = path
            request_payload = request_body if method == "POST" else None
            if method == "GET":
                request_path = f"{path}?{parse.urlencode(request_body)}"

            response = self._request(method, request_path, request_payload)
            page_results = response.get("results", [])
            if not isinstance(page_results, list):
                raise NotionApiError(f"Unexpected paginated response for {path}: results is not a list")
            results.extend(page_results)

            if not response.get("has_more"):
                return results
            cursor = response.get("next_cursor")
            if not cursor:
                raise NotionApiError(f"Notion response for {path} set has_more without next_cursor")

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if method not in {"GET", "POST"}:
            raise NotionApiError(f"Blocked non-read Notion method: {method}")

        url = f"{self.BASE_URL}{path}"
        data = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.notion_version,
            "Content-Type": "application/json",
        }
        if body is not None:
            data = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")

        for attempt in range(1, self.max_retries + 1):
            self.total_requests += 1
            self.total_rate_limit_wait_seconds += self._rate_limiter.wait()
            try:
                req = request.Request(url, data=data, headers=headers, method=method)
                with request.urlopen(req, timeout=self.timeout_seconds) as response:
                    payload = response.read().decode("utf-8")
                    return json.loads(payload) if payload else {}
            except error.HTTPError as exc:
                response_body = exc.read().decode("utf-8", errors="replace")
                if exc.code == 429 or 500 <= exc.code < 600:
                    if attempt < self.max_retries:
                        sleep_for = self._retry_delay(attempt, exc)
                        LOGGER.warning(
                            "Notion API %s %s failed with HTTP %s; retrying in %.1fs",
                            method,
                            path,
                            exc.code,
                            sleep_for,
                        )
                        self.total_retry_wait_seconds += sleep_for
                        time.sleep(sleep_for)
                        continue
                raise NotionApiError(
                    f"Notion API {method} {path} failed: HTTP {exc.code}: {response_body}",
                    status_code=exc.code,
                    body=_parse_error_body(response_body),
                    response_text=response_body,
                ) from exc
            except (TimeoutError, socket.timeout) as exc:
                if attempt < self.max_retries:
                    sleep_for = self._retry_delay(attempt)
                    LOGGER.warning(
                        "Notion API %s %s timed out after %ss; retrying in %.1fs",
                        method,
                        path,
                        self.timeout_seconds,
                        sleep_for,
                    )
                    self.total_retry_wait_seconds += sleep_for
                    time.sleep(sleep_for)
                    continue
                raise NotionApiError(
                    f"Notion API {method} {path} timed out after {self.timeout_seconds}s"
                ) from exc
            except error.URLError as exc:
                if attempt < self.max_retries:
                    sleep_for = self._retry_delay(attempt)
                    LOGGER.warning(
                        "Notion API %s %s failed: %s; retrying in %.1fs",
                        method,
                        path,
                        exc.reason,
                        sleep_for,
                    )
                    self.total_retry_wait_seconds += sleep_for
                    time.sleep(sleep_for)
                    continue
                raise NotionApiError(f"Notion API {method} {path} failed: {exc.reason}") from exc

        raise NotionApiError(f"Notion API {method} {path} failed after {self.max_retries} attempts")

    def _retry_delay(self, attempt: int, exc: error.HTTPError | None = None) -> float:
        if exc is not None:
            retry_after = exc.headers.get("Retry-After")
            if retry_after:
                try:
                    return min(float(retry_after), self.retry_max_sleep_seconds)
                except ValueError:
                    pass
        return min(self.retry_initial_sleep_seconds * (2.0 ** (attempt - 1)), self.retry_max_sleep_seconds)


def normalize_id(value: str) -> str:
    return value.strip().replace("-", "")


def _parse_error_body(response_body: str) -> dict[str, Any]:
    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
