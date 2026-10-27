from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr
from typing import Optional


class Settings(BaseSettings):
    # Telegram
    bot_token: SecretStr = Field(..., description="Токен Telegram бота от @BotFather")
    tg_proxy: Optional[str] = Field(None, alias="TG_PROXY", description="Прокси для Telegram API")
    http_proxy: Optional[str] = Field(None, alias="HTTP_PROXY", description="HTTP прокси")
    https_proxy: Optional[str] = Field(None, alias="HTTPS_PROXY", description="HTTPS прокси")
    
    @property
    def telegram_proxy(self) -> Optional[str]:
        return self.tg_proxy or self.https_proxy or self.http_proxy

    # Яндекс Музыка
    yandex_token: Optional[SecretStr] = Field(None, description="Токен Яндекс Музыки для треков Плюс")
    yandex_proxy: Optional[str] = Field(None, description="Прокси для Яндекса (например, http://user:pass@ip:port)")
    
    # Spotify
    spotify_client_id: Optional[str] = Field(None, description="Spotify Client ID")
    spotify_client_secret: Optional[SecretStr] = Field(None, description="Spotify Client Secret")

    
    # YouTube / yt-dlp
    yt_proxy: Optional[str] = Field(None, description="Прокси для YouTube при необходимости")
    
    # Настройки загрузки
    max_file_size_mb: int = Field(50, description="Максимальный размер файла в МБ (лимит Telegram API)")
    audio_bitrate: str = Field("320", description="Битрейт аудио в kbps (192-320)")
    max_concurrent_downloads: int = Field(2, description="Максимальное число одновременных загрузок")

    # База данных и администрирование
    db_path: str = Field("bot.db", description="Путь к файлу базы данных SQLite")
    admin_ids_str: str = Field("", alias="ADMIN_IDS", description="ID администраторов через запятую")
    admin_id_str: str = Field("", alias="ADMIN_ID", description="Одиночный ID администратора")

    @property
    def admin_ids(self) -> list[int]:
        raw = f"{self.admin_ids_str},{self.admin_id_str}"
        return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


config = Settings()
