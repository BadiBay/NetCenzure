import os
import re
import asyncio
from dataclasses import dataclass
from typing import Optional
from yandex_music import Client
from yandex_music.exceptions import YandexMusicError
from yandex_music.utils.request import Request
from config import config


@dataclass
class TrackMetadata:
    track_id: str
    title: str
    artist: str
    duration_sec: int
    search_query: str
    is_explicit: bool = False
    is_live: bool = False
    is_remix: bool = False


class YandexMusicServiceError(Exception):
    """Базовое исключение для сервиса Яндекс Музыки."""
    pass


class TrackNotFoundError(YandexMusicServiceError):
    pass


class YandexMusicService:
    # Поддерживаем ссылки вида music.yandex.ru/album/123/track/456, короткие ссылки и домены .ru/.com/.by/.kz
    URL_REGEX = re.compile(
        r'https?://(?:music\.yandex\.(?:ru|com|by|kz)|yandex\.ru/yandsearch|ya\.ru)/'
        r'(?:album/(\d+)/)?track/(\d+)'
    )

    def __init__(self):
        self._client: Optional[Client] = None

    def _get_client(self) -> Client:
        """Ленивая инициализация синхронного клиента Яндекса с прокси и токеном."""
        if self._client is None:
            request = None
            if config.yandex_proxy:
                request = Request(proxy_url=config.yandex_proxy)
            else:
                # Отключаем использование глобальных HTTP_PROXY/HTTPS_PROXY для доменов Яндекса,
                # чтобы запросы шли напрямую с российского IP сервера и не вызывали ошибку 451
                no_proxy_domains = 'yandex.ru,yandex.net,yandex.com,ya.ru,yandex.kz,yandex.by,music.yandex.ru'
                os.environ['NO_PROXY'] = no_proxy_domains
                os.environ['no_proxy'] = no_proxy_domains
            
            token = config.yandex_token.get_secret_value() if config.yandex_token else None
            self._client = Client(token=token, request=request)
            self._client.init()
        return self._client

    @classmethod
    def extract_track_id(cls, text: str) -> Optional[str]:
        """Извлекает ID трека из текста сообщения."""
        match = cls.URL_REGEX.search(text)
        if not match:
            return None
        album_id, track_id = match.groups()
        return f"{track_id}:{album_id}" if album_id else track_id

    def _fetch_metadata_sync(self, track_param: str) -> TrackMetadata:
        """Синхронный метод получения метаданных (вызывается в отдельном потоке)."""
        try:
            client = self._get_client()
            tracks = client.tracks([track_param])
            if not tracks or not tracks[0]:
                raise TrackNotFoundError("Трек не найден в каталоге Яндекс Музыки.")
            
            track = tracks[0]
            
            # Формируем название исполнителей
            artists = ", ".join(a.name for a in track.artists if a.name) if track.artists else "Unknown Artist"
            
            # Учитываем версию трека (например, "Remix", "Deluxe", "Remastered")
            title = track.title
            if track.version:
                title = f"{title} ({track.version})"
            
            duration_sec = (track.duration_ms // 1000) if track.duration_ms else 0
            
            # Очищаем название от маркеров цензуры и радиоверсий для поискового запроса на YouTube
            clean_title_for_search = re.sub(
                r'\s*\(?(?:clean|censored|radio edit|edited|radio version|sfw|clean version|цензура)\)?',
                '',
                title,
                flags=re.IGNORECASE
            ).replace("()", "").replace("[]", "").strip()
            
            search_query = f"{artists} - {clean_title_for_search}"

            return TrackMetadata(
                track_id=str(track.id),
                title=title,
                artist=artists,
                duration_sec=duration_sec,
                search_query=search_query
            )
        except YandexMusicError as e:
            raise YandexMusicServiceError(f"Ошибка API Яндекс Музыки: {str(e)}") from e

    async def get_metadata(self, text: str) -> TrackMetadata:
        """Асинхронная обертка для получения метаданных трека по ссылке."""
        track_param = self.extract_track_id(text)
        if not track_param:
            raise ValueError("Некорректная ссылка на Яндекс Музыку.")
        
        # Выполняем синхронный запрос к Яндексу в пуле потоков, чтобы не блокировать event loop
        return await asyncio.to_thread(self._fetch_metadata_sync, track_param)


yandex_service = YandexMusicService()
