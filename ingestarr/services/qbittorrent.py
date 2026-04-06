from __future__ import annotations

import time

from .base import BaseClient, ServiceError


class QBittorrentClient(BaseClient):
    service_name = "qbittorrent"

    def __init__(
        self, base_url: str, username: str, password: str, timeout: int = 30
    ):
        super().__init__(base_url, timeout=timeout)
        self._username = username
        self._password = password
        self._auth_time: float = 0
        self._auth_max_age: float = 3000  # re-auth after 50 min

    def _ensure_auth(self) -> None:
        if time.monotonic() - self._auth_time < self._auth_max_age:
            return
        self._login()

    def _login(self) -> None:
        self.log.debug("Authenticating to qBittorrent")
        try:
            resp = self.session.post(
                f"{self.base_url}/api/v2/auth/login",
                data={"username": self._username, "password": self._password},
                timeout=self.timeout,
            )
        except Exception as exc:
            raise ServiceError(self.service_name, f"Login failed: {exc}") from exc

        if resp.status_code != 200 or resp.text.strip() != "Ok.":
            raise ServiceError(
                self.service_name,
                f"Login failed: HTTP {resp.status_code} — {resp.text[:200]}",
            )
        self._auth_time = time.monotonic()
        self.log.info("qBittorrent authenticated")

    def _authed_request(self, method: str, path: str, **kwargs):
        self._ensure_auth()
        try:
            return self._request(method, path, **kwargs)
        except ServiceError as exc:
            if "403" in str(exc):
                self.log.warning("Session expired, re-authenticating")
                self._auth_time = 0
                self._ensure_auth()
                return self._request(method, path, **kwargs)
            raise

    def add_torrent(
        self, url: str, category: str = "", savepath: str = ""
    ) -> None:
        form: dict = {"urls": url}
        if category:
            form["category"] = category
        if savepath:
            form["savepath"] = savepath

        self._authed_request("POST", "/api/v2/torrents/add", data=form)
        self.log.info("qBittorrent: added torrent, category=%s", category)

    def get_torrents(self, category: str = "") -> list[dict]:
        params: dict = {}
        if category:
            params["category"] = category

        self._ensure_auth()
        resp = self._authed_request("GET", "/api/v2/torrents/info", params=params)
        return resp.json()
