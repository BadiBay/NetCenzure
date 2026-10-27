# Используем официальный slim-образ Python 3.11
FROM python:3.11-slim

# Установка ffmpeg и необходимых системных утилит
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Создаем рабочую директорию
WORKDIR /app

# Настройка переменных окружения Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Установка зависимостей Python
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода проекта
COPY . .

# Создание непривилегированного пользователя для безопасности
RUN useradd -m botuser && chown -R botuser:botuser /app
USER botuser

# Запуск приложения
CMD ["python", "main.py"]
