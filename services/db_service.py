import logging
import aiosqlite
from typing import Optional
from config import config

logger = logging.getLogger(__name__)


class DatabaseService:
    def __init__(self):
        self.db_path = config.db_path

    async def init_db(self):
        """Инициализация базы данных SQLite и создание таблиц."""
        logger.info(f"Инициализация базы данных: {self.db_path}")
        async with aiosqlite.connect(self.db_path) as db:
            # Таблица пользователей
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    downloads_count INTEGER DEFAULT 0
                )
            """)
            # Таблица истории загрузок треков
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tracks_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    artist TEXT,
                    title TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    media_type TEXT DEFAULT 'audio',
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            """)
            
            # Миграция: Добавляем колонку media_type, если её нет (для старых баз)
            try:
                await db.execute("ALTER TABLE tracks_log ADD COLUMN media_type TEXT DEFAULT 'audio'")
                # Обновляем старые записи, где artist похож на видео
                await db.execute("UPDATE tracks_log SET media_type = 'video' WHERE artist LIKE '%Video%'")
                await db.commit()
                logger.info("Миграция: колонка media_type добавлена в tracks_log.")
            except aiosqlite.OperationalError:
                # Колонка уже существует
                pass

            # Таблица кэшированных треков (Telegram file_id)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS file_cache (
                    track_id TEXT PRIMARY KEY,
                    file_id TEXT NOT NULL,
                    artist TEXT,
                    title TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.commit()

    async def register_user(self, user_id: int, username: Optional[str], full_name: str):
        """Регистрирует нового пользователя или обновляет его данные."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO users (user_id, username, full_name)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    full_name = excluded.full_name
            """, (user_id, username or "", full_name))
            await db.commit()

    async def log_download(self, user_id: int, artist: str, title: str, media_type: str = 'audio'):
        """Увеличивает счетчик загрузок пользователя и логирует трек."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE users SET downloads_count = downloads_count + 1
                WHERE user_id = ?
            """, (user_id,))
            await db.execute("""
                INSERT INTO tracks_log (user_id, artist, title, media_type)
                VALUES (?, ?, ?, ?)
            """, (user_id, artist, title, media_type))
            await db.commit()

    async def get_user_stats(self, user_id: int) -> dict:
        """Возвращает персональную и общую статистику для команды /stats."""
        async with aiosqlite.connect(self.db_path) as db:
            # Личная статистика (аудио и видео)
            user_audio = 0
            user_video = 0
            created_at = "неизвестно"
            
            async with db.execute("SELECT created_at FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    created_at = row[0]

            async with db.execute("SELECT media_type, COUNT(*) FROM tracks_log WHERE user_id = ? GROUP BY media_type", (user_id,)) as cursor:
                async for r in cursor:
                    if r[0] == 'audio':
                        user_audio = r[1]
                    elif r[0] == 'video':
                        user_video = r[1]

            # Общее количество скачанных треков и видео в боте
            total_audio = 0
            total_video = 0
            async with db.execute("SELECT media_type, COUNT(*) FROM tracks_log GROUP BY media_type") as cursor:
                async for r in cursor:
                    if r[0] == 'audio':
                        total_audio = r[1]
                    elif r[0] == 'video':
                        total_video = r[1]

            # Топ-3 самых популярных аудио-треков в боте
            top_tracks = []
            async with db.execute("""
                SELECT artist, title, COUNT(*) as cnt
                FROM tracks_log
                WHERE media_type = 'audio'
                GROUP BY artist, title
                ORDER BY cnt DESC
                LIMIT 3
            """) as cursor:
                async for r in cursor:
                    top_tracks.append(f"{r[0]} — {r[1]} ({r[2]} раз(а))")
                    
            return {
                "user_audio": user_audio,
                "user_video": user_video,
                "created_at": created_at,
                "total_audio": total_audio,
                "total_video": total_video,
                "top_tracks": top_tracks
            }

    async def get_admin_stats(self) -> dict:
        """Возвращает расширенную статистику для администратора по команде /admin."""
        async with aiosqlite.connect(self.db_path) as db:
            # Общее количество пользователей
            async with db.execute("SELECT COUNT(*) FROM users") as cursor:
                row = await cursor.fetchone()
                total_users = row[0] if row else 0

            # Пользователи с хотя бы одной загрузкой
            async with db.execute("SELECT COUNT(*) FROM users WHERE downloads_count > 0") as cursor:
                row = await cursor.fetchone()
                active_users = row[0] if row else 0

            # Общее число скачиваний
            async with db.execute("SELECT SUM(downloads_count) FROM users") as cursor:
                row = await cursor.fetchone()
                total_downloads = row[0] if (row and row[0] is not None) else 0

            # Количество кэшированных треков
            async with db.execute("SELECT COUNT(*) FROM file_cache") as cursor:
                row = await cursor.fetchone()
                cached_tracks = row[0] if row else 0

            # Топ-5 самых активных пользователей
            top_users = []
            async with db.execute("""
                SELECT user_id, username, full_name, downloads_count
                FROM users
                ORDER BY downloads_count DESC
                LIMIT 5
            """) as cursor:
                async for r in cursor:
                    name = f"@{r[1]}" if r[1] else r[2]
                    top_users.append(f"{name} (ID: {r[0]}) — {r[3]} трек(ов)")

            return {
                "total_users": total_users,
                "active_users": active_users,
                "total_downloads": total_downloads,
                "cached_tracks": cached_tracks,
                "top_users": top_users
            }

    async def find_users(self, query: str) -> list[dict]:
        """Ищет пользователей по username, ID или имени."""
        clean_query = query.strip().lstrip("@")
        async with aiosqlite.connect(self.db_path) as db:
            users = []
            if clean_query.isdigit():
                sql = """
                    SELECT user_id, username, full_name, downloads_count, created_at
                    FROM users
                    WHERE user_id = ? OR LOWER(username) = LOWER(?)
                """
                params = (int(clean_query), clean_query)
            else:
                sql = """
                    SELECT user_id, username, full_name, downloads_count, created_at
                    FROM users
                    WHERE LOWER(username) = LOWER(?)
                """
                params = (clean_query,)

            async with db.execute(sql, params) as cursor:
                async for r in cursor:
                    users.append({
                        "user_id": r[0],
                        "username": r[1],
                        "full_name": r[2],
                        "downloads_count": r[3],
                        "created_at": r[4]
                    })

            if not users:
                like_query = f"%{clean_query}%"
                async with db.execute("""
                    SELECT user_id, username, full_name, downloads_count, created_at
                    FROM users
                    WHERE LOWER(username) LIKE LOWER(?) OR LOWER(full_name) LIKE LOWER(?)
                    LIMIT 5
                """, (like_query, like_query)) as cursor:
                    async for r in cursor:
                        users.append({
                            "user_id": r[0],
                            "username": r[1],
                            "full_name": r[2],
                            "downloads_count": r[3],
                            "created_at": r[4]
                        })
            return users

    async def get_user_by_id(self, user_id: int) -> Optional[dict]:
        """Возвращает данные пользователя по его ID."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT user_id, username, full_name, downloads_count, created_at
                FROM users
                WHERE user_id = ?
            """, (user_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        "user_id": row[0],
                        "username": row[1],
                        "full_name": row[2],
                        "downloads_count": row[3],
                        "created_at": row[4]
                    }
                return None

    async def get_user_tracks(self, user_id: int, limit: int = 100) -> tuple[list[dict], int]:
        """Возвращает историю скачиваний и общее количество треков пользователя."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM tracks_log WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                total_count = row[0] if row else 0

            tracks = []
            async with db.execute("""
                SELECT artist, title, created_at
                FROM tracks_log
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
            """, (user_id, limit)) as cursor:
                async for r in cursor:
                    tracks.append({
                        "artist": r[0],
                        "title": r[1],
                        "created_at": r[2]
                    })
            return tracks, total_count

    async def get_cached_file(self, track_id: str) -> Optional[dict]:
        """Возвращает кэшированный файл (file_id, artist, title) по ID трека, если он существует."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT file_id, artist, title FROM file_cache WHERE track_id = ?",
                (track_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        "file_id": row[0],
                        "artist": row[1],
                        "title": row[2]
                    }
        return None

    async def save_cached_file(self, track_id: str, file_id: str, artist: str, title: str):
        """Сохраняет Telegram file_id трека в кэш."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO file_cache (track_id, file_id, artist, title)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(track_id) DO UPDATE SET
                    file_id = excluded.file_id,
                    artist = excluded.artist,
                    title = excluded.title
            """, (track_id, file_id, artist, title))
            await db.commit()


db_service = DatabaseService()
