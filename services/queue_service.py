import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional, Callable, Any
from config import config

logger = logging.getLogger(__name__)


class DownloadQueue:
    """Асинхронная очередь для ограничения количества одновременных скачиваний."""
    def __init__(self, max_concurrent: int = 2):
        self.max_concurrent = max_concurrent
        self.active_count = 0
        self.queue: list[dict] = []
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, status_callback: Optional[Callable[[str], Any]] = None):
        """Контекстный менеджер для входа в очередь скачивания."""
        item = None
        acquired = False
        try:
            async with self._lock:
                if self.active_count < self.max_concurrent and not self.queue:
                    self.active_count += 1
                    acquired = True
                else:
                    event = asyncio.Event()
                    item = {
                        'event': event,
                        'status_callback': status_callback
                    }
                    self.queue.append(item)
                    position = len(self.queue)

            if not acquired:
                if status_callback:
                    try:
                        await status_callback(
                            f"⏳ <b>Все загрузчики заняты ({self.max_concurrent}/{self.max_concurrent}).</b>\n"
                            f"Ваш трек <b>№{position}</b> в очереди, подождите..."
                        )
                    except Exception as e:
                        logger.warning(f"Failed to send queue position message: {e}")

                await item['event'].wait()
                acquired = True

            yield
        finally:
            async with self._lock:
                if not acquired and item in self.queue:
                    # Если запрос был отменен в процессе ожидания в очереди
                    self.queue.remove(item)
                    self._notify_queue_positions()
                elif acquired:
                    # Загрузка завершена или прервана
                    if self.queue:
                        next_item = self.queue.pop(0)
                        next_item['event'].set()
                        self._notify_queue_positions()
                    else:
                        self.active_count -= 1

    def _notify_queue_positions(self):
        """Уведомляет оставшихся в очереди пользователей о продвижении."""
        for idx, q_item in enumerate(self.queue, start=1):
            cb = q_item.get('status_callback')
            if cb:
                try:
                    asyncio.create_task(
                        cb(
                            f"⏳ <b>Очередь продвинулась!</b>\n"
                            f"Ваш трек <b>№{idx}</b> в очереди, подождите..."
                        )
                    )
                except Exception as e:
                    logger.warning(f"Failed to update queue position notification: {e}")


download_queue = DownloadQueue(max_concurrent=config.max_concurrent_downloads)
