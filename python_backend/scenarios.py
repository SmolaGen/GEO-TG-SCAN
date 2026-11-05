"""
Synthetic test scenarios for Telegram Live Geo Inspector.
Evaluated purely on the backend via AnomalyDetector.
"""

import math
from typing import List, Dict, Any

def generate_normal_walk() -> List[Dict[str, Any]]:
    points = []
    base_lat, base_lon = 55.751244, 37.618423
    base_time = 1700000000
    for i in range(12):
        lat = base_lat + (i * 0.000091) + ((i % 3) * 0.000012)
        lon = base_lon + (i * 0.000123) - ((i % 2) * 0.000008)
        points.append({
            "contact_id": "test_walk",
            "contact_name": "Пешая прогулка (Алексей)",
            "chat_id": "chat_walk",
            "message_id": 100 + i,
            "point_type": "live",
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "accuracy": round(8.0 + (i % 4) * 2.0, 1),
            "heading": 45 + (i % 3) * 4,
            "live_period": 2147483647,
            "edit_date": base_time + i * 8,
            "received_at": (base_time + i * 8) * 1000,
            "edit_number": i + 1,
            "is_after_gap": False
        })
    return points

def generate_teleportation() -> List[Dict[str, Any]]:
    points = generate_normal_walk()[:4]
    base_time = points[-1]["edit_date"]
    # Sudden jump 55 km north within 4 seconds
    points.append({
        "contact_id": "test_teleport",
        "contact_name": "Телепортация (Скачок 55 км)",
        "chat_id": "chat_teleport",
        "message_id": 105,
        "point_type": "live",
        "lat": 56.251244,
        "lon": 37.618423,
        "accuracy": 10.0,
        "heading": 0,
        "live_period": 2147483647,
        "edit_date": base_time + 4,
        "received_at": (base_time + 4) * 1000,
        "edit_number": 5,
        "is_after_gap": False
    })
    return points

def generate_flight() -> List[Dict[str, Any]]:
    """
    Passenger flight: speed ~850 km/h (>350 km/h) sustained over 8 points along airway.
    Smooth straight motion -> labeled 'возможно, перелёт' with 0 speed penalty, status 'clean'.
    """
    points = []
    base_lat, base_lon = 56.000123, 37.000456
    base_time = 1700000000
    # ~2.36 km per 10 seconds = ~850 km/h
    step_lat = 0.015123
    step_lon = 0.020456
    for i in range(8):
        points.append({
            "contact_id": "test_flight",
            "contact_name": "Авиаперелёт (Шереметьево - Пулково)",
            "chat_id": "chat_flight",
            "message_id": 200 + i,
            "point_type": "live",
            "lat": round(base_lat + i * step_lat, 6),
            "lon": round(base_lon + i * step_lon, 6),
            "accuracy": round(15.0 + (i % 2) * 5.0, 1),
            "heading": 43,
            "live_period": 2147483647,
            "edit_date": base_time + i * 10,
            "received_at": (base_time + i * 10) * 1000,
            "edit_number": i + 1,
            "is_after_gap": False
        })
    return points

def generate_sapsan_train() -> List[Dict[str, Any]]:
    """
    High-speed train Sapsan: speed ~180 km/h (<250 km/h) with natural rail curves and GPS sensor noise.
    Must NOT trigger false alarms.
    """
    points = []
    base_lat, base_lon = 57.000123, 34.000456
    base_time = 1700000000
    # ~500m per 10s = ~180 km/h
    for i in range(10):
        noise_lat = ((i % 5) - 2) * 0.000015
        noise_lon = ((i % 3) - 1) * 0.000018
        points.append({
            "contact_id": "test_sapsan",
            "contact_name": "Поезд «Сапсан» (180 км/ч)",
            "chat_id": "chat_sapsan",
            "message_id": 300 + i,
            "point_type": "live",
            "lat": round(base_lat + i * 0.003512 + noise_lat, 6),
            "lon": round(base_lon + i * 0.002812 + noise_lon, 6),
            "accuracy": round(12.0 + (i % 3) * 3.0, 1),
            "heading": 30,
            "live_period": 2147483647,
            "edit_date": base_time + i * 10,
            "received_at": (base_time + i * 10) * 1000,
            "edit_number": i + 1,
            "is_after_gap": False
        })
    return points

def generate_highway_drive() -> List[Dict[str, Any]]:
    """
    Highway M11 drive: ~125 km/h, realistic variations in bearing, speed, and accuracy.
    Must NOT trigger unnatural_smoothness.
    """
    points = []
    base_lat, base_lon = 56.500123, 36.000456
    curr_time = 1700000000
    for i in range(35):
        noise_lat = (math.sin(i) * 0.000041)
        noise_lon = (math.cos(i) * 0.000042)
        acc_noise = 8.0 + (i % 7) * 1.5
        curr_time += 8 + (i % 3)
        points.append({
            "contact_id": "test_highway",
            "contact_name": "Трасса М11 (Автомобиль 125 км/ч)",
            "chat_id": "chat_highway",
            "message_id": 400 + i,
            "point_type": "live",
            "lat": round(base_lat + i * 0.002812 + noise_lat, 6),
            "lon": round(base_lon + i * 0.002212 + noise_lon, 6),
            "accuracy": round(acc_noise, 1),
            "heading": int((40 + math.sin(i * 0.5) * 5) % 360),
            "live_period": 2147483647,
            "edit_date": curr_time,
            "received_at": curr_time * 1000,
            "edit_number": i + 1,
            "is_after_gap": False
        })
    return points

def generate_tunnel_signal_loss() -> List[Dict[str, Any]]:
    """
    Tunnel: car enters tunnel, 15-minute gap in data, then point arrives after gap.
    Must be tagged as is_after_gap=True without teleportation penalty!
    """
    points = []
    base_lat, base_lon = 55.700123, 37.500456
    base_time = 1700000000
    # 3 points before tunnel
    for i in range(3):
        time_step = 10 + i
        points.append({
            "contact_id": "test_tunnel",
            "contact_name": "Туннель (Пропадание сигнала)",
            "chat_id": "chat_tunnel",
            "message_id": 500 + i,
            "point_type": "live",
            "lat": round(base_lat + i * 0.001123, 6),
            "lon": round(base_lon + i * 0.001123, 6),
            "accuracy": 10.0,
            "heading": 45,
            "live_period": 2147483647,
            "edit_date": base_time + i * time_step,
            "received_at": (base_time + i * time_step) * 1000,
            "edit_number": i + 1,
            "is_after_gap": False
        })
    # Point after tunnel (15 min / 900 s gap, moved 4 km)
    points.append({
        "contact_id": "test_tunnel",
        "contact_name": "Туннель (Пропадание сигнала)",
        "chat_id": "chat_tunnel",
        "message_id": 503,
        "point_type": "live",
        "lat": round(base_lat + 0.035123, 6),
        "lon": round(base_lon + 0.035123, 6),
        "accuracy": 15.0,
        "heading": 45,
        "live_period": 2147483647,
        "edit_date": base_time + 30 + 900,
        "received_at": (base_time + 30 + 900) * 1000,
        "edit_number": 4,
        "is_after_gap": True  # Tagged gap resumption
    })
    return points

def generate_ping_pong() -> List[Dict[str, Any]]:
    points = []
    base_time = 1700000000
    points.append({
        "contact_id": "test_pingpong",
        "contact_name": "Метание (Ping-Pong)",
        "chat_id": "chat_pp",
        "message_id": 601,
        "point_type": "live",
        "lat": 55.751244,
        "lon": 37.618423,
        "accuracy": 12.0,
        "heading": 0,
        "live_period": 2147483647,
        "edit_date": base_time,
        "received_at": base_time * 1000,
        "edit_number": 1,
        "is_after_gap": False
    })
    points.append({
        "contact_id": "test_pingpong",
        "contact_name": "Метание (Ping-Pong)",
        "chat_id": "chat_pp",
        "message_id": 602,
        "point_type": "live",
        "lat": 55.773123,
        "lon": 37.618423,
        "accuracy": 12.0,
        "heading": 0,
        "live_period": 2147483647,
        "edit_date": base_time + 10,
        "received_at": (base_time + 10) * 1000,
        "edit_number": 2,
        "is_after_gap": False
    })
    points.append({
        "contact_id": "test_pingpong",
        "contact_name": "Метание (Ping-Pong)",
        "chat_id": "chat_pp",
        "message_id": 603,
        "point_type": "live",
        "lat": 55.751312,
        "lon": 37.618512,
        "accuracy": 12.0,
        "heading": 0,
        "live_period": 2147483647,
        "edit_date": base_time + 20,
        "received_at": (base_time + 20) * 1000,
        "edit_number": 3,
        "is_after_gap": False
    })
    return points

def generate_frozen_live() -> List[Dict[str, Any]]:
    points = []
    base_time = 1700000000
    for i in range(7):
        points.append({
            "contact_id": "test_frozen",
            "contact_name": "Зависший Live (Frozen)",
            "chat_id": "chat_frozen",
            "message_id": 700 + i,
            "point_type": "live",
            "lat": 55.751234,
            "lon": 37.618456,
            "accuracy": 10.0,
            "heading": 90,
            "live_period": 2147483647,
            "edit_date": base_time + i * 5,
            "received_at": (base_time + i * 5) * 1000,
            "edit_number": i + 1,
            "is_after_gap": False
        })
    return points

SCENARIO_GENERATORS = {
    "normal_walk": {
        "id": "normal_walk",
        "title": "Пешая прогулка (Алексей)",
        "description": "Естественное перемещение пешехода 4-5 км/ч. Ожидается: аномалий не найдено.",
        "expected_status": "clean",
        "generator": generate_normal_walk
    },
    "flight": {
        "id": "flight",
        "title": "Пассажирский авиаперелёт (850 км/ч)",
        "description": "Крейсерская скорость >350 км/ч вдоль трассы. Ожидается: 'возможно, перелёт' без штрафа.",
        "expected_status": "clean",
        "generator": generate_flight
    },
    "sapsan_train": {
        "id": "sapsan_train",
        "title": "Скоростной поезд «Сапсан» (210 км/ч)",
        "description": "Движение по железной дороге с естественным шумом. Ожидается: аномалий не найдено.",
        "expected_status": "clean",
        "generator": generate_sapsan_train
    },
    "highway_drive": {
        "id": "highway_drive",
        "title": "Автомагистраль М11 (125 км/ч)",
        "description": "35 точек на высокой скорости с естественной дисперсией датчиков. Ожидается: аномалий не найдено.",
        "expected_status": "clean",
        "generator": generate_highway_drive
    },
    "tunnel_gap": {
        "id": "tunnel_gap",
        "title": "Туннель и пропадание связи",
        "description": "Перерыв в получении данных 15 минут. Точка после пробела не штрафуется за скорость.",
        "expected_status": "clean",
        "generator": generate_tunnel_signal_loss
    },
    "teleportation": {
        "id": "teleportation",
        "title": "Скачок координат (Телепортация)",
        "description": "Мгновенное перемещение на 55 км за 4 секунды. Ожидается: найдены аномалии.",
        "expected_status": "flagged",
        "generator": generate_teleportation
    },
    "ping_pong": {
        "id": "ping_pong",
        "title": "Метание координат (Ping-Pong)",
        "description": "Бросок на 2.5 км и возврат в исходную точку за 20 секунд (вышка Wi-Fi / spoofing).",
        "expected_status": "suspicious",
        "generator": generate_ping_pong
    },
    "frozen_live": {
        "id": "frozen_live",
        "title": "Зависшие координаты при активных правках",
        "description": "7 правок подряд с абсолютно неизменными координатами (0.0 м дрейфа).",
        "expected_status": "suspicious",
        "generator": generate_frozen_live
    }
}
