import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from config import config
from handlers.track_handler import router as track_router
from services.db_service import db_service


async def main():
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    logger = logging.getLogger("Main")
    logger.info("Запуск бота...")

    # Инициализация базы данных
    await db_service.init_db()

    # Инициализация бота и диспетчера с поддержкой прокси
    session = AiohttpSession(proxy=config.telegram_proxy) if config.telegram_proxy else None
    if config.telegram_proxy:
        logger.info(f"Используется прокси для Telegram API: {config.telegram_proxy}")

    bot = Bot(
        token=config.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session
    )
    
    dp = Dispatcher()
    dp.include_router(track_router)

    try:
        # Удаляем вебхуки и сбрасываем накопленные оффлайн-сообщения
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот завершил работу вручную.")
