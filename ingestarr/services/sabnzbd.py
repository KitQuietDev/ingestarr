from __future__ import annotations

from .base import BaseClient


class SabnzbdClient(BaseClient):
    service_name = "sabnzbd"

    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        super().__init__(base_url, api_key, timeout)

    def add_url(self, url: str, category: str = "") -> str:
        """Add an NZB URL to SABnzbd. Returns nzo_id."""
        params: dict = {
            "mode": "addurl",
            "name": url,
            "apikey": self.api_key,
            "output": "json",
        }
        if category:
            params["cat"] = category

        resp = self.get("/api", params=params)
        nzo_ids = resp.get("nzo_ids", [])
        nzo_id = nzo_ids[0] if nzo_ids else ""
        self.log.info("SABnzbd added URL, nzo_id=%s", nzo_id)
        return nzo_id

    def get_queue(self) -> list[dict]:
        """Get current download queue."""
        resp = self.get(
            "/api",
            params={"mode": "queue", "apikey": self.api_key, "output": "json"},
        )
        return resp.get("queue", {}).get("slots", [])

    def get_history(self, limit: int = 50) -> list[dict]:
        """Get download history."""
        resp = self.get(
            "/api",
            params={
                "mode": "history",
                "apikey": self.api_key,
                "output": "json",
                "limit": limit,
            },
        )
        return resp.get("history", {}).get("slots", [])
