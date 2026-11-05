#!/usr/bin/env bash
set -e

echo "=== Telegram Live Geo Inspector (Local Server) ==="
echo "Запуск строго на 127.0.0.1 без внешнего доступа"

# 1. Check Python version
if ! command -v python3 &> /dev/null; then
    echo "Ошибка: python3 не найден в системе."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Обнаружен Python: $PYTHON_VERSION"

# 2. Ensure data directory with 0700 permissions
mkdir -p data
if [ "$(uname)" = "Linux" ] || [ "$(uname)" = "Darwin" ]; then
    chmod 0700 data 2>/dev/null || true
fi

# 3. Setup virtual environment if needed
if [ ! -d "venv" ]; then
    echo "Создание виртуального окружения venv..."
    python3 -m venv venv || {
        echo "Попытка установки зависимостей в текущее окружение..."
    }
fi

if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

# 4. Install dependencies
echo "Проверка и установка зависимостей..."
if [ -f "python_backend/requirements.txt" ]; then
    pip install -r python_backend/requirements.txt --break-system-packages 2>/dev/null || pip install -r python_backend/requirements.txt
elif [ -f "requirements.txt" ]; then
    pip install -r requirements.txt --break-system-packages 2>/dev/null || pip install -r requirements.txt
fi

# 5. Run test suite
echo "Запуск тестов детектора и безопасности..."
python3 -m unittest discover -s python_backend/tests

# 6. Launch FastAPI local daemon
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"
echo "Запуск FastAPI uvicorn сервера на ${HOST}:${PORT}..."
exec uvicorn python_backend.main:app --host "${HOST}" --port "${PORT}"
