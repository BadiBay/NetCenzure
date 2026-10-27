import os
import re
import time
import asyncio
import logging
from pathlib import Path
from typing import Optional, Callable, Any
import yt_dlp
from yt_dlp.utils import DownloadError
from config import config

logger = logging.getLogger(__name__)


class DownloaderError(Exception):
    """Базовое исключение загрузчика."""
    pass


class FileSizeExceededError(DownloaderError):
    """Исключение при превышении лимита размера файла в Telegram."""
    pass


class YouTubeDownloader:
    def _add_cookies_if_exists(self, ydl_opts: dict):
        """Подключает файл cookies.txt, если он существует (для Instagram)."""
        cookies_paths = [Path("/app/data/cookies.txt"), Path("data/cookies.txt"), Path("cookies.txt")]
        for cp in cookies_paths:
            if cp.exists():
                ydl_opts['cookiefile'] = str(cp)
                logger.info(f"Используем cookies из файла: {cp}")
                break

    def _score_candidate(self, info: dict, query: str, target_duration: int) -> int:
        """Алгоритм оценки кандидатов для выбора версии без цензуры."""
        score = 0
        title = info.get('title', '')
        title_lower = title.lower()
        duration = info.get('duration') or 0
        channel = (info.get('uploader') or info.get('channel') or '').lower()
        query_lower = query.lower()

        # 0. ПРОВЕРКА РЕЛЕВАНТНОСТИ НАЗВАНИЯ (отсеиваем совершенно другие треки того же исполнителя)
        # Извлекаем слова из запроса (без коротких предлогов и символов)
        query_words = [w.lower() for w in re.findall(r'\w+', query) if len(w) > 2]
        if query_words:
            matched_words = sum(1 for w in query_words if w in title_lower)
            if matched_words == 0:
                score -= 300  # Ни одно слово из названия не совпало — это чужой трек!
            elif matched_words == len(query_words):
                score += 50   # Полное совпадение всех слов запроса

        # 1. ШТРАФ ЗА ЦЕНЗУРУ / CLEAN ВЕРСИИ (-200 баллов)
        clean_markers = ['clean', 'censored', 'radio edit', 'edited', 'radio version', 'sfw', 'clean version', 'цензура']
        if any(w in title_lower for w in clean_markers):
            score -= 200

        # 2. БОНУС ЗА EXPLICIT / ОРИГИНАЛ (+100 баллов)
        explicit_markers = ['explicit', 'uncensored', 'dirty', 'original', 'album version', 'parental advisory', 'без цензуры', 'без купюр', '18+']
        if any(w in title_lower for w in explicit_markers):
            score += 100

        # 3. СРАВНЕНИЕ ДЛИТЕЛЬНОСТИ С ЯНДЕКС МУЗЫКОЙ
        if target_duration > 0 and duration > 0:
            diff = abs(duration - target_duration)
            if diff <= 3:
                score += 40  # Идеальное совпадение по хронометражу
            elif diff <= 10:
                score += 25
            elif diff <= 20:
                score += 10
            elif diff > 60:
                score -= 150  # Разница > минуты (клипы со вступлениями, интервью, лупы)

        # 4. БОНУС ЗА ОФИЦИАЛЬНЫЕ АУДИО И ТОПИК-КАНАЛЫ
        audio_markers = ['audio', 'topic', 'official audio', 'lyric', 'lyrics', 'hq', 'remastered']
        if any(w in title_lower for w in audio_markers):
            score += 30

        if 'topic' in channel or 'vevo' in channel or 'official' in channel:
            score += 20

        # 5. ШТРАФ ЗА ИСКАЖЕННЫЕ ВЕРСИИ И ЛАЙВЫ (если их не было в исходном запросе)
        bad_words = ['live', 'concert', 'cover', 'reaction', 'tutorial', 'karaoke', 'instrumental', 'slowed', 'reverb', 'sped up', 'bass boosted', 'remix', 'tiktok', '8d']
        for bw in bad_words:
            if bw in title_lower and bw not in query_lower:
                score -= 100

        return score

    def _find_best_video(self, ydl: yt_dlp.YoutubeDL, query: str, target_duration: int) -> Optional[dict]:
        """Ищет топ-10 кандидатов, оценивает их и возвращает лучшую версию без цензуры."""
        # Ищем 3 лучших кандидата (вместо 20), чтобы поиск проходил за 1-2 секунды вместо 15
        search_queries = [
            f"ytsearch3:{query} audio",
            f"ytsearch3:{query}"
        ]
        
        candidates_map = {}
        for sq in search_queries:
            try:
                info = ydl.extract_info(sq, download=False)
                if info and 'entries' in info:
                    for entry in info['entries']:
                        if entry and entry.get('id'):
                            candidates_map[entry['id']] = entry
            except Exception as e:
                logger.warning(f"Ошибка при поиске '{sq}': {e}")

        if not candidates_map:
            return None

        candidates = list(candidates_map.values())
        for c in candidates:
            c['_score'] = self._score_candidate(c, query, target_duration)

        # Сортируем по убыванию рейтинга
        candidates.sort(key=lambda x: x.get('_score', 0), reverse=True)
        
        best = candidates[0]
        logger.info(
            f"Лучший кандидат для '{query}': '{best.get('title')}' "
            f"(Длительность: {best.get('duration')}с, Рейтинг: {best.get('_score')})"
        )
        return best

    def _download_sync(
        self,
        query: str,
        output_dir: str,
        target_duration: int = 0,
        status_callback: Optional[Callable[[str], Any]] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None
    ) -> str:
        """Синхронный поиск и скачивание аудио с YouTube через yt-dlp."""
        def notify(text: str):
            if status_callback and loop:
                try:
                    asyncio.run_coroutine_threadsafe(status_callback(text), loop)
                except Exception as e:
                    logger.warning(f"Failed to send status callback: {e}")

        out_tmpl = os.path.join(output_dir, '%(id)s.%(ext)s')
        current_title = ["Аудиопоток"]
        last_update_time = [0.0]
        last_percent = [-10]

        def progress_hook(d):
            status = d.get('status')
            if status == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes', 0)
                if total > 0:
                    percent = min(100, int(downloaded / total * 100))
                else:
                    pct_str = re.sub(r'[^0-9.]', '', str(d.get('_percent_str', '0')))
                    percent = int(float(pct_str)) if pct_str else 0

                now = time.time()
                # Обновляем сообщение не чаще раза в 2 секунды И при изменении минимум на 8% (защита от лимита 429)
                if (now - last_update_time[0] >= 2.0 and abs(percent - last_percent[0]) >= 8) or (percent == 100 and last_percent[0] != 100):
                    last_update_time[0] = now
                    last_percent[0] = percent
                    
                    bar_len = 10
                    filled = int(bar_len * percent // 100)
                    bar = "█" * filled + "░" * (bar_len - filled)
                    
                    speed = d.get('_speed_str', '').strip()
                    speed_text = f" | ⚡ {speed}" if speed and speed != "Unknown" else ""
                    
                    notify(
                        f"🎯 <b>Найдено на YouTube:</b>\n<code>{current_title[0]}</code>\n\n"
                        f"⬇️ <b>Загрузка:</b> [{bar}] <b>{percent}%</b>\n"
                        f"<i>{speed_text}</i>"
                    )
            elif status == 'finished':
                notify(
                    f"🎯 <b>Найдено на YouTube:</b>\n<code>{current_title[0]}</code>\n\n"
                    f"⚙️ <b>Обработка аудио...</b>\n"
                    f"<i>[██████████] 100% — Конвертация в MP3 (320 kbps)...</i>"
                )

        ydl_opts = {
            'format': 'bestaudio/bestaudio*',
            'outtmpl': out_tmpl,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': config.audio_bitrate,
            }],
            'max_filesize': config.max_file_size_mb * 1024 * 1024,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'socket_timeout': 15,
            'retries': 5,
            'fragment_retries': 5,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'ios', 'tv']
                }
            },
            'progress_hooks': [progress_hook],
        }
        
        proxy_url = config.yt_proxy or os.getenv("YT_PROXY")
        if proxy_url:
            ydl_opts['proxy'] = proxy_url
        else:
            # Пустая строка заставляет yt-dlp игнорировать глобальные HTTP_PROXY/HTTPS_PROXY из окружения,
            # чтобы YouTube шел напрямую с сервера без блокировок со стороны прокси Telegram!
            ydl_opts['proxy'] = ''
            current_no_proxy = os.environ.get('NO_PROXY', '')
            yt_domains = 'youtube.com,www.youtube.com,googlevideo.com,youtu.be'
            if yt_domains not in current_no_proxy:
                os.environ['NO_PROXY'] = f"{current_no_proxy},{yt_domains}" if current_no_proxy else yt_domains
                os.environ['no_proxy'] = os.environ['NO_PROXY']

        self._add_cookies_if_exists(ydl_opts)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                if query.startswith("http://") or query.startswith("https://"):
                    target_url = query
                    current_title[0] = query
                    notify(
                        f"🎯 <b>Прямая ссылка YouTube:</b>\n<code>{query}</code>\n\n"
                        f"⬇️ <b>Загрузка:</b> [░░░░░░░░░░] <b>0%</b>\n"
                        f"<i>Подключение к потоку...</i>"
                    )
                else:
                    # 1. Умный выбор лучшей версии без цензуры
                    notify(f"🔎 <b>Ищу лучшую версию на YouTube...</b>\n<i>Запрос: {query}</i>")
                    best_candidate = self._find_best_video(ydl, query, target_duration)
                    
                    if best_candidate and best_candidate.get('webpage_url'):
                        target_url = best_candidate['webpage_url']
                        title = best_candidate.get('title', query)
                        dur = best_candidate.get('duration', 0)
                        dur_str = f" ({dur}с)" if dur else ""
                        current_title[0] = f"{title}{dur_str}"
                        notify(
                            f"🎯 <b>Найдено на YouTube:</b>\n<code>{current_title[0]}</code>\n\n"
                            f"⬇️ <b>Загрузка:</b> [░░░░░░░░░░] <b>0%</b>\n"
                            f"<i>Подключение к потоку...</i>"
                        )
                    else:
                        target_url = f"ytsearch1:{query}"
                        current_title[0] = query
                        notify(
                            f"🎯 <b>Найдено на YouTube:</b>\n<code>{current_title[0]}</code>\n\n"
                            f"⬇️ <b>Загрузка:</b> [░░░░░░░░░░] <b>0%</b>\n"
                            f"<i>Подключение к потоку...</i>"
                        )
                
                logger.info(f"Скачивание видео по URL: {target_url}")
                info = ydl.extract_info(target_url, download=True)
                if info and 'entries' in info:
                    info = info['entries'][0]
                
                if not info:
                    raise DownloaderError("Не удалось скачать аудио с YouTube (ошибка соединения с YouTube или видео заблокировано).")
                
                # Постобработчик FFmpeg меняет расширение на mp3
                filename = ydl.prepare_filename(info)
                mp3_path = Path(filename).with_suffix('.mp3')
                
                if not mp3_path.exists():
                    # Fallback проверка, если имя файла отличается
                    files = list(Path(output_dir).glob("*.mp3"))
                    if files:
                        mp3_path = files[0]
                    else:
                        raise DownloaderError("Не удалось найти сконвертированный MP3 файл.")

                # Финальная проверка размера файла
                file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
                if file_size_mb > config.max_file_size_mb:
                    if mp3_path.exists():
                        mp3_path.unlink()
                    raise FileSizeExceededError(
                        f"Размер файла ({file_size_mb:.1f} МБ) превышает лимит Telegram ({config.max_file_size_mb} МБ)."
                    )

                return str(mp3_path), info
                
        except DownloadError as e:
            logger.error(f"yt-dlp download error for query '{query}': {e}")
            raise DownloaderError("Не удалось найти или скачать аудио на YouTube.") from e

    async def download_audio(
        self,
        query: str,
        output_dir: str,
        target_duration: int = 0,
        status_callback: Optional[Callable[[str], Any]] = None
    ) -> tuple[str, dict]:
        """Асинхронно скачивает трек в указанную временную папку."""
        loop = asyncio.get_running_loop() if status_callback else None
        return await asyncio.to_thread(
            self._download_sync, query, output_dir, target_duration, status_callback, loop
        )

    def _download_video_sync(
        self,
        url: str,
        output_dir: str,
        status_callback: Optional[Callable[[str], Any]] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None
    ) -> tuple[str, dict]:
        def notify(text: str):
            if status_callback and loop:
                try:
                    asyncio.run_coroutine_threadsafe(status_callback(text), loop)
                except Exception as e:
                    logger.warning(f"Failed to send status callback: {e}")

        out_tmpl = os.path.join(output_dir, '%(id)s.%(ext)s')
        last_update_time = [0.0]
        last_percent = [-10]
        current_title = ["Видеопоток"]

        def progress_hook(d):
            status = d.get('status')
            if status == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes', 0)
                if total > 0:
                    percent = min(100, int(downloaded / total * 100))
                else:
                    pct_str = re.sub(r'[^0-9.]', '', str(d.get('_percent_str', '0')))
                    percent = int(float(pct_str)) if pct_str else 0

                now = time.time()
                if (now - last_update_time[0] >= 2.0 and abs(percent - last_percent[0]) >= 8) or (percent == 100 and last_percent[0] != 100):
                    last_update_time[0] = now
                    last_percent[0] = percent
                    
                    bar_len = 10
                    filled = int(bar_len * percent // 100)
                    bar = "█" * filled + "░" * (bar_len - filled)
                    
                    speed = d.get('_speed_str', '').strip()
                    speed_text = f" | ⚡ {speed}" if speed and speed != "Unknown" else ""
                    
                    notify(
                        f"🎯 <b>Загрузка видео:</b>\n<code>{current_title[0]}</code>\n\n"
                        f"⬇️ <b>Прогресс:</b> [{bar}] <b>{percent}%</b>\n"
                        f"<i>{speed_text}</i>"
                    )

        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': out_tmpl,
            'max_filesize': config.max_file_size_mb * 1024 * 1024,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'socket_timeout': 15,
            'retries': 5,
            'fragment_retries': 5,
            'progress_hooks': [progress_hook],
            'extractor_args': {
                'youtube': {'player_client': ['android', 'ios', 'tv']},
                'tiktok': {'api_hostname': 'api16-normal-c-useast1a.tiktokv.com'}
            },
        }

        proxy_url = config.yt_proxy or os.getenv("YT_PROXY")
        if proxy_url:
            ydl_opts['proxy'] = proxy_url
        else:
            ydl_opts['proxy'] = ''
            current_no_proxy = os.environ.get('NO_PROXY', '')
            yt_domains = 'youtube.com,www.youtube.com,googlevideo.com,youtu.be,tiktok.com,instagram.com'
            if yt_domains not in current_no_proxy:
                os.environ['NO_PROXY'] = f"{current_no_proxy},{yt_domains}" if current_no_proxy else yt_domains
                os.environ['no_proxy'] = os.environ['NO_PROXY']

        self._add_cookies_if_exists(ydl_opts)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                notify(
                    f"🎯 <b>Подключение к сервису...</b>\n<code>{url}</code>\n\n"
                    f"⬇️ <b>Загрузка:</b> [░░░░░░░░░░] <b>0%</b>\n"
                    f"<i>Получение информации о видео...</i>"
                )
                
                # 1. Извлекаем информацию о видео без скачивания
                info_meta = ydl.extract_info(url, download=False)
                if info_meta and 'entries' in info_meta:
                    info_meta = info_meta['entries'][0]
                
                if not info_meta:
                    raise DownloaderError("Не удалось получить информацию о видео. Возможно, ссылка недействительна или видео удалено.")
                
                title = info_meta.get('title') or "Видео"
                current_title[0] = title
                
                # 2. Проверка длительности (10 минут = 600 секунд)
                duration = info_meta.get('duration') or 0
                if duration > 600:
                    dur_min = int(duration) // 60
                    raise DownloaderError(f"Видео слишком длинное ({dur_min} мин). Разрешено скачивать видео не длиннее 10 минут.")

                # 3. Скачивание
                info = ydl.extract_info(url, download=True)
                if info and 'entries' in info:
                    info = info['entries'][0]
                
                if not info:
                    raise DownloaderError("Не удалось скачать видео. Возможно, требуется авторизация или доступ ограничен.")
                
                filename = ydl.prepare_filename(info)
                file_path = Path(filename)
                
                if not file_path.exists():
                    files = list(Path(output_dir).glob("*"))
                    if files:
                        file_path = files[0]
                    else:
                        raise DownloaderError("Не удалось найти скачанный файл.")

                file_size_mb = file_path.stat().st_size / (1024 * 1024)
                if file_size_mb > config.max_file_size_mb:
                    if file_path.exists():
                        file_path.unlink()
                    raise FileSizeExceededError(
                        f"Размер видео ({file_size_mb:.1f} МБ) превышает лимит Telegram ({config.max_file_size_mb} МБ)."
                    )

                return str(file_path), info
                
        except DownloadError as e:
            logger.error(f"yt-dlp video download error for url '{url}': {e}")
            err_str = str(e)
            if "слишком длинное" in err_str:
                match = re.search(r'Видео слишком длинное.*', err_str)
                msg = match.group(0) if match else "Видео превышает лимит длительности (10 минут)."
                raise DownloaderError(msg) from e
            elif "max-filesize" in err_str.lower() or "file is larger than" in err_str.lower():
                raise FileSizeExceededError(f"Размер видео превышает лимит в {config.max_file_size_mb} МБ.")
            else:
                clean_msg = re.sub(r'^ERROR:\s*\[[^\]]+\]\s*', '', err_str).strip()
                raise DownloaderError(clean_msg if clean_msg else "Не удалось скачать видео. Проверьте правильность ссылки.") from e

    async def download_video(
        self,
        url: str,
        output_dir: str,
        status_callback: Optional[Callable[[str], Any]] = None
    ) -> tuple[str, dict]:
        """Асинхронно скачивает видео во временную папку."""
        loop = asyncio.get_running_loop() if status_callback else None
        return await asyncio.to_thread(
            self._download_video_sync, url, output_dir, status_callback, loop
        )



youtube_downloader = YouTubeDownloader()
