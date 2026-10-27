import re
from enum import Enum, auto

class ServiceType(Enum):
    YANDEX_MUSIC = auto()
    SPOTIFY = auto()
    YOUTUBE_VIDEO = auto()
    YOUTUBE_MUSIC = auto()
    TIKTOK = auto()
    INSTAGRAM = auto()
    UNKNOWN = auto()

class LinkRouter:
    YANDEX_REGEX = re.compile(r'https?://(?:music\.yandex\.(?:ru|com|by|kz)|yandex\.ru/yandsearch|ya\.ru)/(?:album/\d+/)?track/\d+')
    SPOTIFY_REGEX = re.compile(r'https?://open\.spotify\.com/(?:intl-[a-z]{2}/)?track/([a-zA-Z0-9]+)')
    YT_MUSIC_REGEX = re.compile(r'https?://music\.youtube\.com/watch\?v=[a-zA-Z0-9_-]{11}')
    YT_VIDEO_REGEX = re.compile(r'(?:https?://)?(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/)|youtu\.be/)[a-zA-Z0-9_-]{11}')
    TIKTOK_REGEX = re.compile(r'https?://(?:www\.)?(?:tiktok\.com/|vt\.tiktok\.com/|vm\.tiktok\.com/)')
    INSTAGRAM_REGEX = re.compile(r'https?://(?:www\.)?instagram\.com/(?:reels?|p|share)/')

    @classmethod
    def detect_service(cls, text: str) -> ServiceType:
        if cls.YANDEX_REGEX.search(text):
            return ServiceType.YANDEX_MUSIC
        elif cls.SPOTIFY_REGEX.search(text):
            return ServiceType.SPOTIFY
        elif cls.YT_MUSIC_REGEX.search(text):
            return ServiceType.YOUTUBE_MUSIC
        elif cls.TIKTOK_REGEX.search(text):
            return ServiceType.TIKTOK
        elif cls.INSTAGRAM_REGEX.search(text):
            return ServiceType.INSTAGRAM
        elif cls.YT_VIDEO_REGEX.search(text):
            return ServiceType.YOUTUBE_VIDEO
        return ServiceType.UNKNOWN
