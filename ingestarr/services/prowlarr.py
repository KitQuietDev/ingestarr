from __future__ import annotations

from ..models import SearchResult
from .base import BaseClient


class ProwlarrClient(BaseClient):
    service_name = "prowlarr"

    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        super().__init__(base_url, api_key, timeout)

    def search(
        self, query: str, categories: list[int] | None = None
    ) -> list[SearchResult]:
        params: dict = {"query": query, "type": "search"}
        if categories:
            params["categories"] = categories

        results_raw = self.get("/api/v1/search", params=params)
        results = []
        for r in results_raw:
            protocol = ""
            if r.get("protocol"):
                protocol = r["protocol"].lower()
            elif r.get("downloadUrl", "").startswith("magnet:"):
                protocol = "torrent"

            results.append(
                SearchResult(
                    title=r.get("title", ""),
                    indexer=r.get("indexer", ""),
                    size_bytes=r.get("size", 0),
                    download_url=r.get("downloadUrl", r.get("guid", "")),
                    seeders=r.get("seeders", 0) or 0,
                    protocol=protocol,
                    category=str(r.get("categories", [{}])[0].get("name", ""))
                    if r.get("categories")
                    else "",
                    guid=r.get("guid", ""),
                )
            )

        self.log.info("Prowlarr search '%s' returned %d results", query, len(results))
        return results
