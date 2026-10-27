import re
import asyncio
import logging
from typing import Optional
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from spotipy.exceptions import SpotifyException

from config import config
from services.yandex_service import TrackMetadata

logger = logging.getLogger(__name__)

class SpotifyServiceError(Exception):
    pass

class SpotifyTrackNotFoundError(SpotifyServiceError):
    pass

class SpotifyService:
    URL_REGEX = re.compile(r'https?://open\.spotify\.com/track/([a-zA-Z0-9]+)')

    def __init__(self):
        self._client: Optional[spotipy.Spotify] = None

    def _get_client(self) -> spotipy.Spotify:
        if self._client is None:
            if not config.spotify_client_id or not config.spotify_client_secret:
                raise SpotifyServiceError("Spotify API ключи не настроены.")
            
            proxy_to_use = config.yt_proxy
            proxies = None
            if proxy_to_use:
                proxies = {"http": proxy_to_use, "https": proxy_to_use}
                
            auth_manager = SpotifyClientCredentials(
                client_id=config.spotify_client_id,
                client_secret=config.spotify_client_secret.get_secret_value(),
                proxies=proxies
            )
            self._client = spotipy.Spotify(auth_manager=auth_manager, proxies=proxies)
        return self._client

    @classmethod
    def extract_track_id(cls, text: str) -> Optional[str]:
        match = cls.URL_REGEX.search(text)
        if not match:
            return None
        return match.group(1)

    def _fetch_metadata_sync(self, track_id: str) -> TrackMetadata:
        try:
            client = self._get_client()
            track = client.track(track_id)
            if not track:
                raise SpotifyTrackNotFoundError("Трек не найден в Spotify.")
            
            artists = ", ".join([artist['name'] for artist in track.get('artists', [])])
            title = track.get('name', 'Unknown')
            duration_sec = track.get('duration_ms', 0) // 1000
            
            clean_title_for_search = re.sub(
                r'\s*\(?(?:clean|censored|radio edit|edited|radio version|sfw|clean version|цензура)\)?',
                '',
                title,
                flags=re.IGNORECASE
            ).replace("()", "").replace("[]", "").strip()
            
            search_query = f"{artists} - {clean_title_for_search}"
            
            return TrackMetadata(
                track_id=f"sp:{track_id}",
                title=title,
                artist=artists,
                duration_sec=duration_sec,
                search_query=search_query
            )
        except SpotifyException as e:
            raise SpotifyServiceError(f"Ошибка API Spotify: {e}") from e
        except Exception as e:
            if isinstance(e, SpotifyServiceError):
                raise
            raise SpotifyServiceError(f"Ошибка при работе со Spotify: {e}") from e

    async def get_metadata(self, text: str) -> TrackMetadata:
        track_id = self.extract_track_id(text)
        if not track_id:
            raise ValueError("Некорректная ссылка на Spotify.")
        return await asyncio.to_thread(self._fetch_metadata_sync, track_id)

spotify_service = SpotifyService()
