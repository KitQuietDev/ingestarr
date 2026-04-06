from __future__ import annotations

from typing import Any

from .base import BaseClient, ServiceError


class ArrClient(BaseClient):
    """Base client for *arr APIs (Radarr, Sonarr, Lidarr, Readarr).

    Subclasses set service_name, api_root, lookup_path, item_path,
    command_path, and external_id_field.
    """

    api_root: str = "/api/v3"
    lookup_path: str = ""       # e.g. "/movie/lookup"
    item_path: str = ""         # e.g. "/movie"
    command_path: str = ""      # e.g. "/command"
    external_id_field: str = "" # e.g. "tmdbId"

    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        super().__init__(base_url, api_key, timeout)

    def lookup(self, term: str) -> list[dict]:
        return self.get(
            f"{self.api_root}{self.lookup_path}",
            params={"term": term},
        )

    def get_all(self) -> list[dict]:
        return self.get(f"{self.api_root}{self.item_path}")

    def get_by_id(self, item_id: int) -> dict:
        return self.get(f"{self.api_root}{self.item_path}/{item_id}")

    def find_existing(self, external_id: int | str) -> dict | None:
        """Check if an item already exists by external ID."""
        items = self.get_all()
        for item in items:
            if item.get(self.external_id_field) == external_id:
                return item
        return None

    def add_item(self, payload: dict) -> dict:
        """Add an item. Returns the created item dict."""
        try:
            return self.post(f"{self.api_root}{self.item_path}", json_data=payload)
        except ServiceError as exc:
            if "already been added" in str(exc).lower():
                self.log.warning("Item already exists: %s", payload.get("title", "?"))
                raise
            raise

    def set_monitored(self, item_id: int, monitored: bool = True) -> dict:
        item = self.get_by_id(item_id)
        item["monitored"] = monitored
        resp = self._request(
            "PUT",
            f"{self.base_url}{self.api_root}{self.item_path}/{item_id}",
            json_data=item,
        )
        return resp.json()

    def trigger_search(self, item_ids: list[int], command_name: str) -> dict:
        """Trigger a search command for given item IDs."""
        payload: dict[str, Any] = {"name": command_name}
        # Different *arrs use different payload keys
        if len(item_ids) == 1:
            # Singular key varies by *arr but most accept the list form too
            pass
        payload[self._search_id_key()] = item_ids
        return self.post(f"{self.api_root}{self.command_path}", json_data=payload)

    def _search_id_key(self) -> str:
        """Override in subclasses for the correct search command payload key."""
        return "ids"


class RadarrClient(ArrClient):
    service_name = "radarr"
    lookup_path = "/movie/lookup"
    item_path = "/movie"
    command_path = "/command"
    external_id_field = "tmdbId"

    def _search_id_key(self) -> str:
        return "movieIds"

    def add_movie(
        self,
        lookup_result: dict,
        quality_profile_id: int,
        root_folder_path: str,
        monitored: bool = True,
        search_on_add: bool = True,
    ) -> dict:
        payload = {
            "title": lookup_result["title"],
            "tmdbId": lookup_result["tmdbId"],
            "year": lookup_result.get("year", 0),
            "qualityProfileId": quality_profile_id,
            "rootFolderPath": root_folder_path,
            "monitored": monitored,
            "addOptions": {"searchForMovie": search_on_add},
        }
        # Merge required fields from lookup
        for key in ("titleSlug", "images"):
            if key in lookup_result:
                payload[key] = lookup_result[key]
        return self.add_item(payload)


class SonarrClient(ArrClient):
    service_name = "sonarr"
    lookup_path = "/series/lookup"
    item_path = "/series"
    command_path = "/command"
    external_id_field = "tvdbId"

    def _search_id_key(self) -> str:
        return "seriesIds"

    def add_series(
        self,
        lookup_result: dict,
        quality_profile_id: int,
        root_folder_path: str,
        seasons: list[int] | None = None,
        monitored: bool = True,
        search_on_add: bool = True,
    ) -> dict:
        payload = {
            "title": lookup_result["title"],
            "tvdbId": lookup_result["tvdbId"],
            "year": lookup_result.get("year", 0),
            "qualityProfileId": quality_profile_id,
            "rootFolderPath": root_folder_path,
            "monitored": monitored,
            "addOptions": {
                "searchForMissingEpisodes": search_on_add,
                "searchForCutoffUnmetEpisodes": False,
            },
        }
        for key in ("titleSlug", "images", "seasons"):
            if key in lookup_result:
                payload[key] = lookup_result[key]

        # If specific seasons requested, only monitor those
        if seasons is not None and "seasons" in payload:
            for s in payload["seasons"]:
                s["monitored"] = s["seasonNumber"] in seasons

        return self.add_item(payload)


class LidarrClient(ArrClient):
    service_name = "lidarr"
    api_root = "/api/v1"
    lookup_path = "/search"
    item_path = "/artist"
    command_path = "/command"
    external_id_field = "foreignArtistId"

    def _search_id_key(self) -> str:
        return "artistIds"

    def lookup(self, term: str) -> list[dict]:
        # Lidarr's search endpoint is different
        return self.get(
            f"{self.api_root}{self.lookup_path}",
            params={"term": term},
        )

    def lookup_album(self, term: str) -> list[dict]:
        return self.get(
            f"{self.api_root}/album/lookup",
            params={"term": term},
        )

    def add_artist(
        self,
        lookup_result: dict,
        quality_profile_id: int,
        metadata_profile_id: int,
        root_folder_path: str,
        monitored: bool = True,
        search_on_add: bool = True,
    ) -> dict:
        artist_data = lookup_result.get("artist", lookup_result)
        payload = {
            "artistName": artist_data.get("artistName", ""),
            "foreignArtistId": artist_data.get("foreignArtistId", ""),
            "qualityProfileId": quality_profile_id,
            "metadataProfileId": metadata_profile_id,
            "rootFolderPath": root_folder_path,
            "monitored": monitored,
            "addOptions": {"searchForMissingAlbums": search_on_add},
        }
        return self.add_item(payload)


class ReadarrClient(ArrClient):
    service_name = "readarr"
    api_root = "/api/v1"
    lookup_path = "/book/lookup"
    item_path = "/book"
    command_path = "/command"
    external_id_field = "foreignBookId"

    def _search_id_key(self) -> str:
        return "bookIds"

    def lookup(self, term: str) -> list[dict]:
        return self.get(
            f"{self.api_root}{self.lookup_path}",
            params={"term": term},
        )

    def add_book(
        self,
        lookup_result: dict,
        quality_profile_id: int,
        metadata_profile_id: int,
        root_folder_path: str,
        monitored: bool = True,
        search_on_add: bool = True,
    ) -> dict:
        author = lookup_result.get("author", {})
        payload = {
            "title": lookup_result.get("title", ""),
            "foreignBookId": lookup_result.get("foreignBookId", ""),
            "author": author,
            "qualityProfileId": quality_profile_id,
            "metadataProfileId": metadata_profile_id,
            "rootFolderPath": root_folder_path,
            "monitored": monitored,
            "addOptions": {"searchForNewBook": search_on_add},
        }
        return self.add_item(payload)
