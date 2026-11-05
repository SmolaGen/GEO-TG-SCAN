import os
import secrets
from dataclasses import dataclass, field
from typing import Dict, Any

@dataclass
class DetectorConfig:
    # Правило 1: Телепортация и перелёты
    max_credible_speed_kmh: float = 250.0       # Порог для наземного транспорта
    flight_speed_threshold_kmh: float = 350.0   # Порог скорости крейсерского полёта
    flight_min_points: int = 4                  # Мин. точек подряд на высокой скорости для перелёта
    teleport_min_distance_m: float = 2000.0     # Скачок на км за секунды (всегда аномалия)

    # Правило 2: Метание (Ping-Pong)
    ping_pong_min_dist_m: float = 1000.0
    ping_pong_max_time_s: float = 60.0

    # Правило 3: Курс компаса
    heading_min_speed_kmh: float = 5.0
    heading_diff_threshold_deg: float = 65.0

    # Правило 4: Искусственная гладкость
    smoothness_min_points: int = 30             # Выборка не менее 30 точек
    smoothness_max_speed_std: float = 0.05      # Нулевой шум скорости (км/ч)
    smoothness_max_bearing_std: float = 0.10    # Нулевой шум азимута (град)

    # Правило 5: Точность
    accuracy_min_realistic_m: float = 1.0
    accuracy_fixed_reps: int = 8
    accuracy_jump_delta_m: float = 500.0

    # Правило 6: Разрядность
    min_decimal_places: int = 4

    # Правило 7: Статичность
    frozen_min_edits: int = 4

    # Правило 8: Тайминг (edit_date)
    timing_max_weight: int = 5                  # Вес не более 5 баллов
    timing_metronome_interval_s: int = 1        # Секундное разрешение Telegram

    # Правило 10: Разрыв непрерывности
    type_discontinuity_dist_m: float = 1000.0

    # Правило 11: Перезапуск сессии
    session_gap_max_speed_kmh: float = 150.0

    # Правило 12: Невозможное место
    check_impossible_location: bool = True

    # Статусы свежести
    stale_timeout_s: float = 180.0

@dataclass
class AppConfig:
    host: str = "127.0.0.1"
    port: int = int(os.getenv("PORT", "8000"))
    data_dir: str = os.getenv("TG_GEO_DATA_DIR", "data")
    db_path: str = os.path.join(os.getenv("TG_GEO_DATA_DIR", "data"), "tg_geo_history.sqlite3")
    session_file: str = os.path.join(os.getenv("TG_GEO_DATA_DIR", "data"), "tg_user_session.session")
    
    # Лимиты Telegram MTProto
    scan_dialog_limit: int = 30
    scan_msg_limit: int = 20
    scan_delay_s: float = 0.5

    # Безопасность
    auth_token: str = field(default_factory=lambda: "")

    def __post_init__(self):
        token_path = os.path.join(self.data_dir, "auth_token")
        if not self.auth_token:
            if os.path.exists(token_path):
                try:
                    with open(token_path, "r", encoding="utf-8") as f:
                        self.auth_token = f.read().strip()
                except Exception:
                    pass
            if not self.auth_token:
                self.auth_token = secrets.token_urlsafe(32)
                try:
                    os.makedirs(self.data_dir, exist_ok=True)
                    try:
                        os.chmod(self.data_dir, 0o700)
                    except Exception:
                        pass
                    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with open(fd, "w", encoding="utf-8") as f:
                        f.write(self.auth_token)
                    try:
                        os.chmod(token_path, 0o600)
                    except Exception:
                        pass
                except Exception:
                    pass

    allowed_origins: list = field(default_factory=lambda: [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://localhost:3000"
    ])
    allowed_hosts: list = field(default_factory=lambda: [
        "127.0.0.1:8000",
        "localhost:8000",
        "127.0.0.1",
        "localhost",
        "127.0.0.1:3000",
        "localhost:3000"
    ])

    detector: DetectorConfig = field(default_factory=DetectorConfig)

    def get_config_json_path(self) -> str:
        return os.path.join(self.data_dir, "config.json")

    def load_detector_config_from_file(self) -> None:
        path = self.get_config_json_path()
        if not os.path.isfile(path):
            return
        try:
            import json
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    if hasattr(self.detector, k):
                        target_type = type(getattr(self.detector, k))
                        try:
                            setattr(self.detector, k, target_type(v))
                        except Exception:
                            pass
        except Exception as e:
            print(f"[Config] Error loading {path}: {e}")

    def save_detector_config_to_file(self) -> None:
        import json
        os.makedirs(self.data_dir, exist_ok=True)
        try:
            os.chmod(self.data_dir, 0o700)
        except Exception:
            pass
        path = self.get_config_json_path()
        data = {
            k: getattr(self.detector, k)
            for k in dir(self.detector)
            if not k.startswith("_") and not callable(getattr(self.detector, k))
        }
        # Запись во временный файл и атомарная замена с правами 0600
        temp_path = path + ".tmp"
        # O_WRONLY | O_CREAT | O_TRUNC с дескриптором 0600
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, path)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass

config = AppConfig()
config.load_detector_config_from_file()
