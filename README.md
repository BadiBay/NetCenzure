# Telegram-бот для скачивания музыки без цензуры (Yandex Music -> YouTube)

Профессионально структурированный асинхронный Telegram-бот на стеке **Python 3.11+, aiogram 3.x, yandex-music и yt-dlp**.

Бот принимает ссылку на трек из Яндекс Музыки, извлекает метаданные и скачивает оригинальную (без цензуры) версию в формате MP3 (320 kbps) с YouTube, после чего отправляет пользователю и автоматически очищает временные файлы.

## Структура проекта
```text
c:\NetZenzure\
├── services/
│   ├── __init__.py
│   ├── yandex_service.py    # Парсер Яндекс Музыки (с поддержкой прокси и токена)
│   └── downloader.py        # Загрузчик yt-dlp + ffmpeg
├── handlers/
│   ├── __init__.py
│   └── track_handler.py     # Обработчик сообщений и логика отправки
├── config.py                # Валидация окружения через Pydantic
├── main.py                  # Точка входа в приложение
├── requirements.txt         # Зависимости
├── Dockerfile               # Контейнеризация (с установкой FFmpeg)
├── docker-compose.yml       # Удобный запуск
└── .env.example             # Пример переменных окружения
```

## Быстрый запуск

### 1. Настройка окружения
Скопируйте файл `.env.example` в `.env` и укажите ваш токен бота от `@BotFather`:
```bash
cp .env.example .env
```

### 2. Запуск через Docker (Рекомендуется)
Убедитесь, что установлены Docker и Docker Compose:
```bash
docker compose up -d --build
```
Просмотр логов:
```bash
docker compose logs -f
```

### 3. Локальный запуск (без Docker)
Для локального запуска убедитесь, что в системе установлен **FFmpeg** (`ffmpeg`).
```bash
python -m venv venv
# Windows: venv\Scripts\activate
# Linux/macOS: source venv/bin/activate

pip install -r requirements.txt
python main.py
```
