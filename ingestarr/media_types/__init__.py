from __future__ import annotations

from ..models import MediaType
from .audiobook import AudiobookHandler
from .base import DirectHandler, HandoffHandler
from .book import BookHandler
from .movie import MovieHandler
from .music import MusicHandler
from .tv import TvHandler

TYPE_HANDLERS: dict[str, HandoffHandler | DirectHandler] = {
    MediaType.BOOK.value: BookHandler(),
    MediaType.AUDIOBOOK.value: AudiobookHandler(),
    MediaType.MOVIE.value: MovieHandler(),
    MediaType.TV.value: TvHandler(),
    MediaType.MUSIC.value: MusicHandler(),
}


def get_handler(media_type: str) -> HandoffHandler | DirectHandler:
    handler = TYPE_HANDLERS.get(media_type)
    if handler is None:
        raise ValueError(f"No handler for media type: {media_type}")
    return handler
