import os
import re
import logging
import tempfile
import hashlib
from pathlib import Path
from aiogram import Router, F, Bot
from aiogram.types import Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramAPIError

from config import config
from services.db_service import db_service
from services.queue_service import download_queue
from services.yandex_service import (
    yandex_service,
    YandexMusicService,
    TrackNotFoundError,
    YandexMusicServiceError
)
from services.spotify_service import spotify_service, SpotifyTrackNotFoundError, SpotifyServiceError
from services.link_router import LinkRouter, ServiceType
from services.downloader import youtube_downloader, DownloaderError, FileSizeExceededError

logger = logging.getLogger(__name__)
router = Router()


class AdminStates(StatesGroup):
    waiting_for_username = State()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext = None):
    if state:
        await state.clear()
    await db_service.register_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    await message.answer(
        "👋 <b>Привет!</b> Я бот для скачивания музыки и видео из различных сервисов.\n\n"
        "🔗 Пришли мне ссылку на трек в <b>Яндекс Музыке</b> или <b>Spotify</b>, а я найду оригинальную версию (без цензуры) в лучшем качестве и мгновенно отправлю тебе аудиофайл.\n\n"
        "🔗 Или отправь мне ссылку на видео из <b>YouTube</b>, <b>TikTok</b> или <b>Instagram</b> и я пришлю тебе видео без водяных знаков в ответ.\n\n"
        "📊 Команда /stats — показать статистику загрузок."
    )


@router.message(Command("stats"))
async def cmd_stats(message: Message, state: FSMContext = None):
    if state:
        await state.clear()
    await db_service.register_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    stats = await db_service.get_user_stats(message.from_user.id)
    
    top_tracks_text = "\n".join(f"  {i+1}. <code>{t}</code>" for i, t in enumerate(stats['top_tracks'])) or "  <i>Пока нет данных</i>"
    
    user_total = stats['user_audio'] + stats['user_video']
    bot_total = stats['total_audio'] + stats['total_video']

    text = (
        f"📊 <b>Твоя статистика:</b>\n\n"
        f"👤 <b>Скачано тобой:</b> <code>{user_total}</code> файлов\n"
        f"   🎵 <i>Музыки:</i> <code>{stats['user_audio']}</code>\n"
        f"   🎬 <i>Видео:</i> <code>{stats['user_video']}</code>\n"
        f"📅 <b>Впервые запущен:</b> <code>{stats['created_at'][:10]}</code>\n\n"
        f"🌐 <b>Всего скачано всеми:</b> <code>{bot_total}</code> файлов\n"
        f"   🎵 <i>Музыки:</i> <code>{stats['total_audio']}</code>\n"
        f"   🎬 <i>Видео:</i> <code>{stats['total_video']}</code>\n\n"
        f"🔥 <b>Топ скачиваемых треков:</b>\n{top_tracks_text}"
    )
    await message.answer(text)


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext = None):
    if state:
        await state.clear()
    if message.from_user.id not in config.admin_ids:
        await message.reply(
            f"⛔ <b>Доступ запрещен!</b>\n"
            f"Ваш Telegram ID (<code>{message.from_user.id}</code>) не найден в списке администраторов."
        )
        return

    stats = await db_service.get_admin_stats()
    top_users_text = "\n".join(f"  {i+1}. {u}" for i, u in enumerate(stats['top_users'])) or "  <i>Пока нет данных</i>"
    
    text = (
        f"🛡 <b>Панель администратора:</b>\n\n"
        f"👥 <b>Всего пользователей:</b> <code>{stats['total_users']}</code>\n"
        f"⚡ <b>Активных (с загрузками):</b> <code>{stats['active_users']}</code>\n"
        f"💾 <b>В кэше (по file_id):</b> <code>{stats['cached_tracks']}</code> трек(ов)\n"
        f"📥 <b>Всего загружено треков:</b> <code>{stats['total_downloads']}</code>\n\n"
        f"🏆 <b>Самые активные пользователи:</b>\n{top_users_text}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Треки пользователя", callback_data="admin_user_tracks")]
    ])
    await message.answer(text, reply_markup=keyboard)


async def send_user_tracks_history(send_func, user: dict):
    tracks, total_count = await db_service.get_user_tracks(user["user_id"], limit=50)
    
    user_name_str = f"@{user['username']}" if user.get('username') else (user.get('full_name') or 'Неизвестно')
    text_lines = [
        f"👤 <b>История скачиваний пользователя:</b> {user_name_str}",
        f"🆔 <b>ID:</b> <code>{user['user_id']}</code>",
        f"📅 <b>Дата регистрации:</b> <code>{str(user.get('created_at', ''))[:10]}</code>",
        f"📈 <b>Всего скачиваний:</b> <code>{total_count}</code>",
        ""
    ]
    
    if not tracks:
        text_lines.append("<i>Пользователь пока не скачивал треки.</i>")
    else:
        text_lines.append("🎵 <b>Последние скачанные треки:</b>")
        for i, t in enumerate(tracks, 1):
            artist_title = f"{t['artist']} — {t['title']}"
            if len(artist_title) > 60:
                artist_title = artist_title[:57] + "..."
            date_str = str(t['created_at'])[:16] if t.get('created_at') else ""
            line = f"  {i}. <code>{artist_title}</code> <i>({date_str})</i>"
            if len("\n".join(text_lines) + "\n" + line) > 3800:
                text_lines.append(f"\n<i>... и еще {total_count - i + 1} трек(ов) (достигнут лимит длины сообщения).</i>")
                break
            text_lines.append(line)
        
        if total_count > len(tracks) and "достигнут лимит" not in text_lines[-1]:
            text_lines.append(f"\n<i>... и еще {total_count - len(tracks)} трек(ов). Показаны последние {len(tracks)}.</i>")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Искать другого пользователя", callback_data="admin_user_tracks")]
    ])
    
    await send_func("\n".join(text_lines), reply_markup=keyboard)


@router.callback_query(F.data == "admin_user_tracks")
async def callback_admin_user_tracks(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.admin_ids:
        await callback.answer("⛔ Доступ запрещен!", show_alert=True)
        return
    
    await callback.answer()
    await state.set_state(AdminStates.waiting_for_username)
    
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel")]
    ])
    await callback.message.answer(
        "👤 <b>Поиск истории скачиваний пользователя</b>\n\n"
        "Введите <b>username</b> (например: <code>@username</code> или <code>username</code>) "
        "или <b>Telegram ID</b> пользователя:",
        reply_markup=cancel_kb
    )


@router.callback_query(F.data == "admin_cancel")
async def callback_admin_cancel(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.admin_ids:
        await callback.answer("⛔ Доступ запрещен!", show_alert=True)
        return
    
    await state.clear()
    await callback.message.edit_text("🚫 <b>Поиск отменен.</b>")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_u_tr:"))
async def callback_admin_user_tracks_select(callback: CallbackQuery):
    if callback.from_user.id not in config.admin_ids:
        await callback.answer("⛔ Доступ запрещен!", show_alert=True)
        return
    
    user_id = int(callback.data.split(":")[1])
    user = await db_service.get_user_by_id(user_id)
    if not user:
        await callback.answer("❌ Пользователь не найден.", show_alert=True)
        return
    
    await callback.answer()
    await send_user_tracks_history(callback.message.answer, user)


@router.message(AdminStates.waiting_for_username)
async def process_admin_username_search(message: Message, state: FSMContext):
    if message.from_user.id not in config.admin_ids:
        await state.clear()
        return
    
    query = message.text.strip()
    if query.startswith("/"):
        await state.clear()
        if query == "/cancel":
            await message.answer("🚫 <b>Поиск отменен.</b>")
        elif query == "/admin":
            await cmd_admin(message, state)
        else:
            await message.answer(f"🚫 <b>Поиск отменен.</b>\nДля выполнения команды <code>{query}</code> отправьте её ещё раз.")
        return

    await state.clear()
    users = await db_service.find_users(query)
    
    if not users:
        await message.answer(
            f"❌ Пользователь <code>{query}</code> не найден в базе данных.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔍 Попробовать снова", callback_data="admin_user_tracks")]
            ])
        )
        return
    
    if len(users) == 1:
        await send_user_tracks_history(message.answer, users[0])
    else:
        buttons = []
        for u in users:
            name = f"@{u['username']}" if u['username'] else (u['full_name'] or f"ID {u['user_id']}")
            buttons.append([InlineKeyboardButton(
                text=f"👤 {name} (ID: {u['user_id']}) — {u['downloads_count']} тр.",
                callback_data=f"admin_u_tr:{u['user_id']}"
            )])
        buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel")])
        await message.answer(
            f"🔍 По запросу <code>{query}</code> найдено несколько пользователей.\nВыберите нужного:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )


@router.message(F.text, StateFilter(None))
async def handle_media_link(message: Message, bot: Bot):
    await db_service.register_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    text = message.text.strip()
    
    service_type = LinkRouter.detect_service(text)
    
    if service_type == ServiceType.UNKNOWN:
        await message.reply(
            "⚠️ Пожалуйста, пришлите корректную ссылку.\n"
            "Поддерживаемые сервисы: <b>Яндекс Музыка, Spotify, TikTok, Instagram, YouTube</b>."
        )
        return

    # Определяем track_id для кэширования
    if service_type == ServiceType.YANDEX_MUSIC:
        ym_id = YandexMusicService.extract_track_id(text)
        track_id = f"ym:{ym_id}" if ym_id else f"ym:{hashlib.md5(text.encode()).hexdigest()}"
    elif service_type == ServiceType.SPOTIFY:
        sp_id = spotify_service.extract_track_id(text)
        track_id = f"sp:{sp_id}" if sp_id else f"sp:{hashlib.md5(text.encode()).hexdigest()}"
    elif service_type == ServiceType.YOUTUBE_MUSIC:
        yt_match = re.search(r'v=([a-zA-Z0-9_-]{11})', text)
        track_id = f"yta:{yt_match.group(1)}" if yt_match else f"yta:{hashlib.md5(text.encode()).hexdigest()}"
    elif service_type == ServiceType.YOUTUBE_VIDEO:
        yt_match = re.search(r'(?:v=|shorts/|youtu\.be/)([a-zA-Z0-9_-]{11})', text)
        track_id = f"ytv:{yt_match.group(1)}" if yt_match else f"ytv:{hashlib.md5(text.encode()).hexdigest()}"
    elif service_type == ServiceType.TIKTOK:
        track_id = f"tt:{hashlib.md5(text.encode()).hexdigest()}"
    elif service_type == ServiceType.INSTAGRAM:
        track_id = f"ig:{hashlib.md5(text.encode()).hexdigest()}"

    is_video_flow = track_id.startswith(("ytv:", "tt:", "ig:"))

    # Кэширование через Telegram file_id
    cached_file = await db_service.get_cached_file(track_id)
    if cached_file:
        try:
            if is_video_flow:
                await bot.send_chat_action(chat_id=message.chat.id, action="upload_video")
                await message.answer_video(
                    video=cached_file['file_id'],
                    caption=f"🎥 <b>{cached_file['title']}</b>\n\n<i>Скачано через @{(await bot.get_me()).username}</i>"
                )
            else:
                await bot.send_chat_action(chat_id=message.chat.id, action="upload_document")
                await message.answer_audio(
                    audio=cached_file['file_id'],
                    performer=cached_file['artist'],
                    title=cached_file['title'],
                    caption=f"🎵 <b>{cached_file['artist']} — {cached_file['title']}</b>\n\n<i>Скачано через @{(await bot.get_me()).username}</i>"
                )
            await db_service.log_download(message.from_user.id, cached_file['artist'] or "Video", cached_file['title'])
            return
        except TelegramAPIError as e:
            logger.warning(f"Failed to send cached file_id {cached_file['file_id']} (will re-download): {e}")

    status_msg = await message.reply("⏳ <b>Обработка запроса...</b>")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        media_path = None
        try:
            artist, title, duration_sec, search_query = "Unknown", "Media", 0, text
            header_text = f"🔍 <b>Обработка ссылки:</b> <code>{text}</code>"

            if not is_video_flow:
                # AUDIO FLOW
                if service_type == ServiceType.YANDEX_MUSIC:
                    await status_msg.edit_text("⏳ <b>Извлекаю метаданные из Яндекс Музыки...</b>")
                    metadata = await yandex_service.get_metadata(text)
                    artist, title = metadata.artist, metadata.title
                    duration_sec, search_query = metadata.duration_sec, metadata.search_query
                    header_text = f"🔍 <b>Трек:</b> <code>{artist} — {title}</code>"
                elif service_type == ServiceType.SPOTIFY:
                    await status_msg.edit_text("⏳ <b>Извлекаю метаданные из Spotify...</b>")
                    metadata = await spotify_service.get_metadata(text)
                    artist, title = metadata.artist, metadata.title
                    duration_sec, search_query = metadata.duration_sec, metadata.search_query
                    header_text = f"🔍 <b>Трек:</b> <code>{artist} — {title}</code>"
                elif service_type == ServiceType.YOUTUBE_MUSIC:
                    artist, title = "YouTube Music", "Audio"
                    header_text = f"🔍 <b>YouTube Music URL:</b> <code>{text}</code>"
            else:
                # VIDEO FLOW
                if service_type == ServiceType.TIKTOK:
                    header_text = f"🔍 <b>TikTok URL:</b> <code>{text}</code>"
                elif service_type == ServiceType.INSTAGRAM:
                    header_text = f"🔍 <b>Instagram URL:</b> <code>{text}</code>"
                elif service_type == ServiceType.YOUTUBE_VIDEO:
                    header_text = f"🔍 <b>YouTube URL:</b> <code>{text}</code>"
            
            await status_msg.edit_text(f"{header_text}\n\n⏳ <i>Ожидание свободной очереди скачивания...</i>")

            async def update_status(status_text: str):
                try:
                    await status_msg.edit_text(f"{header_text}\n\n{status_text}")
                except Exception as e:
                    logger.warning(f"Failed to update status message: {e}")

            async with download_queue.acquire(status_callback=update_status):
                if not is_video_flow:
                    media_path, media_info = await youtube_downloader.download_audio(
                        query=search_query,
                        output_dir=temp_dir,
                        target_duration=duration_sec,
                        status_callback=update_status
                    )
                    if service_type == ServiceType.YOUTUBE_MUSIC and media_info:
                        artist = media_info.get('artist') or media_info.get('uploader') or "YouTube"
                        title = media_info.get('track') or media_info.get('title') or "Audio"
                        duration_sec = media_info.get('duration') or 0
                else:
                    media_path, media_info = await youtube_downloader.download_video(
                        url=text,
                        output_dir=temp_dir,
                        status_callback=update_status
                    )
                    if media_info:
                        title = media_info.get('title') or "Video"
                        artist = media_info.get('uploader') or "Video"

                await status_msg.edit_text(f"{header_text}\n\n📤 <b>Загружаю файл в Telegram...</b>")

                safe_title = f"{artist} - {title}".replace("/", "_").replace("\\", "_")
                
                if not is_video_flow:
                    media_file = FSInputFile(path=media_path, filename=f"{safe_title}.mp3")
                    await bot.send_chat_action(chat_id=message.chat.id, action="upload_document")
                    sent_msg = await message.answer_audio(
                        audio=media_file,
                        performer=artist,
                        title=title,
                        duration=duration_sec,
                        caption=f"🎵 <b>{artist} — {title}</b>\n\n<i>Скачано через @{(await bot.get_me()).username}</i>"
                    )
                    if sent_msg.audio and sent_msg.audio.file_id:
                        await db_service.save_cached_file(track_id, sent_msg.audio.file_id, artist, title)
                else:
                    media_file = FSInputFile(path=media_path, filename=f"{safe_title}.mp4")
                    await bot.send_chat_action(chat_id=message.chat.id, action="upload_video")
                    sent_msg = await message.answer_video(
                        video=media_file,
                        caption=f"🎥 <b>{title}</b>\n\n<i>Скачано через @{(await bot.get_me()).username}</i>"
                    )
                    if sent_msg.video and sent_msg.video.file_id:
                        await db_service.save_cached_file(track_id, sent_msg.video.file_id, artist, title)

            if media_path and os.path.exists(media_path):
                try:
                    os.remove(media_path)
                except Exception as e:
                    logger.error(f"Ошибка при удалении файла {media_path}: {e}")

            await db_service.log_download(
                message.from_user.id, 
                artist, 
                title, 
                media_type='video' if is_video_flow else 'audio'
            )
            await status_msg.delete()

        except (TrackNotFoundError, SpotifyTrackNotFoundError):
            await status_msg.edit_text("❌ <b>Ошибка:</b> Трек не найден (или доступ к нему ограничен).")
        except (YandexMusicServiceError, SpotifyServiceError) as e:
            logger.error(f"Music service error: {e}")
            await status_msg.edit_text("❌ <b>Ошибка сервиса:</b> Попробуйте позже или проверьте ссылку.")
        except FileSizeExceededError as e:
            await status_msg.edit_text(f"❌ <b>Превышен лимит размера:</b>\n<i>{e}</i>")
        except DownloaderError as e:
            logger.error(f"Downloader error: {e}")
            await status_msg.edit_text(f"❌ <b>Не удалось скачать:</b>\n<i>{e}</i>")
        except TelegramAPIError as e:
            logger.error(f"Telegram API error: {e}")
            await status_msg.edit_text("❌ <b>Ошибка при отправке файла в Telegram.</b> Возможно, файл слишком велик.")
        except Exception as e:
            logger.exception(f"Unexpected error while processing track: {e}")
            await status_msg.edit_text("❌ <b>Произошла непредвиденная ошибка.</b> Мы уже изучаем проблему.")
        finally:
            if media_path and os.path.exists(media_path):
                try:
                    os.remove(media_path)
                except Exception as e:
                    pass
