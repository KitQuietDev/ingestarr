from __future__ import annotations

import logging
from typing import Any

import requests


class ServiceError(Exception):
    def __init__(self, service: str, message: str):
        self.service = service
        super().__init__(f"[{service}] {message}")


class BaseClient:
    """Thin HTTP client base with session reuse, timeout, and logging."""

    service_name: str = "unknown"

    def __init__(self, base_url: str, api_key: str = "", timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.log = logging.getLogger(f"ingestarr.services.{self.service_name}")

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self.api_key:
            h["X-Api-Key"] = self.api_key
        return h

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: Any = None,
        data: Any = None,
        extra_headers: dict[str, str] | None = None,
    ) -> requests.Response:
        url = f"{self.base_url}{path}"
        headers = self._headers()
        if extra_headers:
            headers.update(extra_headers)

        self.log.debug("%s %s params=%s", method, url, params)

        try:
            resp = self.session.request(
                method,
                url,
                params=params,
                json=json_data,
                data=data,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ServiceError(self.service_name, f"Request failed: {exc}") from exc

        if resp.status_code >= 400:
            self.log.error(
                "%s %s → %d: %s", method, url, resp.status_code, resp.text[:500]
            )
            raise ServiceError(
                self.service_name,
                f"HTTP {resp.status_code}: {resp.text[:200]}",
            )

        return resp

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params).json()

    def post(
        self,
        path: str,
        json_data: Any = None,
        data: Any = None,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        resp = self._request(
            "POST", path, params=params, json_data=json_data,
            data=data, extra_headers=extra_headers,
        )
        if resp.content:
            return resp.json()
        return None
