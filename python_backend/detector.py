"""
Single Source of Truth: Anomaly Detector Engine for Telegram Live Geolocation.
Handles all 12 rules, geodesy, flight detection, smoothness on transport, gap handling, and timing.
"""

import math
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any

EARTH_RADIUS_METERS = 6371000.0

def haversine_distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (math.sin(delta_phi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_METERS * c

def calculate_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_lambda = math.radians(lon2 - lon1)

    y = math.sin(delta_lambda) * math.cos(phi2)
    x = (math.cos(phi1) * math.sin(phi2) -
         math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda))
    theta = math.atan2(y, x)
    return (math.degrees(theta) + 360.0) % 360.0

def angular_difference_deg(angle1: float, angle2: float) -> float:
    diff = abs((angle1 % 360.0) - (angle2 % 360.0))
    return min(diff, 360.0 - diff)

def calculate_speed_kmh(distance_meters: float, delta_seconds: float) -> float:
    if delta_seconds <= 0.001:
        return 0.0
    return (distance_meters / delta_seconds) * 3.6

def get_decimal_places(val: float) -> int:
    s = f"{val:.8f}".rstrip("0")
    if "." in s:
        return len(s.split(".")[1])
    return 0

def standard_deviation(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)

@dataclass
class AnomalyFlag:
    rule_id: str
    rule_name: str
    weight: int
    explanation: str
    observed_value: str
    threshold: str

@dataclass
class PointEvaluation:
    distance_from_prev_m: float
    speed_kmh: float
    bearing_deg: Optional[float]
    flags: List[AnomalyFlag]
    score: int
    overall_status: str  # 'clean', 'suspicious', 'flagged'
    status_text: str
    is_flight: bool = False
    is_after_gap: bool = False

class AnomalyDetector:
    def __init__(self, config=None):
        if config is None:
            try:
                from .config import DetectorConfig
            except (ImportError, ValueError):
                from config import DetectorConfig
            self.cfg = DetectorConfig()
        else:
            self.cfg = config

    def evaluate_point(self, current: Dict[str, Any], history: List[Dict[str, Any]]) -> PointEvaluation:
        flags: List[AnomalyFlag] = []
        prev = history[-1] if history else None

        distance_m = 0.0
        speed_kmh = 0.0
        bearing_deg: Optional[float] = None
        delta_seconds = 1.0

        is_after_gap = current.get("is_after_gap", False)
        is_flight = False

        if prev:
            distance_m = haversine_distance_meters(
                prev["lat"], prev["lon"], current["lat"], current["lon"]
            )
            
            # Telegram edit_date is primary (integer seconds)
            edit_delta = current.get("edit_date", 0) - prev.get("edit_date", 0)
            recv_delta = (current.get("received_at", 0) - prev.get("received_at", 0)) / 1000.0
            
            delta_seconds = float(edit_delta if edit_delta > 0 else (recv_delta if recv_delta > 0.1 else 1.0))
            speed_kmh = calculate_speed_kmh(distance_m, delta_seconds)

            if distance_m > 0.5:
                bearing_deg = calculate_bearing_deg(
                    prev["lat"], prev["lon"], current["lat"], current["lon"]
                )
            else:
                bearing_deg = prev.get("bearing_deg")

        # --- GAP CHECK: If this point arrived after a data gap, do NOT penalize distance/speed ---
        if is_after_gap:
            flags.append(AnomalyFlag(
                rule_id="data_gap_resumption",
                rule_name="Скачок после пробела (данных нет)",
                weight=0,
                explanation=f"Точка получена после перерыва в получении данных ({delta_seconds:.0f} с). Смещение на {distance_m:.0f} м не штрафуется за скорость, так как промежуточные точки отсутствуют.",
                observed_value=f"Пробел {delta_seconds:.0f} с, {distance_m:.0f} м",
                threshold="Возобновление после пробела"
            ))

        # --- RULE 1: ТЕЛЕПОРТАЦИЯ ИЛИ ПЕРЕЛЁТ ---
        elif prev and current.get("point_type") == "live" and prev.get("point_type") == "live":
            acc1 = prev.get("accuracy") or 15.0
            acc2 = current.get("accuracy") or 15.0
            accuracy_sum = acc1 + acc2

            # Фильтр суммарной погрешности: скачок внутри кругов точности НЕ считается аномалией
            if distance_m > accuracy_sum:
                # Мгновенный скачок за секунды на километры (всегда аномалия)
                instant_teleport_leap = distance_m >= self.cfg.teleport_min_distance_m and delta_seconds <= 5.0

                # Признак перелёта: устойчивая высокая скорость (>350 км/ч до 1150 км/ч)
                is_high_speed = (
                    not instant_teleport_leap and
                    speed_kmh >= self.cfg.flight_speed_threshold_kmh and
                    speed_kmh <= 1150.0
                )

                if is_high_speed:
                    # Проверяем серию точек высокой скорости (cfg.flight_min_points)
                    recent_speeds = [p.get("speed_kmh", 0) for p in history[-(self.cfg.flight_min_points - 1):]] if history else []
                    has_flight_series = len(recent_speeds) >= (self.cfg.flight_min_points - 1) and all(
                        s >= self.cfg.flight_speed_threshold_kmh * 0.8 for s in recent_speeds
                    )
                    
                    if has_flight_series or len(history) < self.cfg.flight_min_points:
                        is_flight = True
                        flags.append(AnomalyFlag(
                            rule_id="possible_flight",
                            rule_name="Возможно, перелёт (авиа)",
                            weight=0, # Не начисляем штрафные баллы за скорость при перелёте
                            explanation=f"Скорость {speed_kmh:.0f} км/ч ({distance_m:.0f} м за {delta_seconds:.1f} с) характерна для авиаперелёта. Баллы за скорость не начисляются.",
                            observed_value=f"{speed_kmh:.0f} км/ч",
                            threshold=f"> {self.cfg.flight_speed_threshold_kmh:.0f} км/ч"
                        ))
                    else:
                        # Одиночный изолированный скачок скорости без серии - телепортация
                        flags.append(AnomalyFlag(
                            rule_id="teleportation",
                            rule_name="Телепортация координат",
                            weight=45,
                            explanation=f"Скорость перемещения {speed_kmh:.0f} км/ч без устойчивой траектории полёта превышает порог.",
                            observed_value=f"{speed_kmh:.0f} км/ч",
                            threshold=f"{self.cfg.max_credible_speed_kmh:.0f} км/ч"
                        ))
                elif instant_teleport_leap or speed_kmh > self.cfg.max_credible_speed_kmh:
                    flags.append(AnomalyFlag(
                        rule_id="teleportation",
                        rule_name="Телепортация координат",
                        weight=45,
                        explanation=f"Скорость перемещения {speed_kmh:.0f} км/ч ({distance_m:.0f} м за {delta_seconds:.1f} с) при сумме радиусов точности {accuracy_sum:.0f} м превышает правдоподобный наземный порог.",
                        observed_value=f"{speed_kmh:.0f} км/ч ({distance_m:.0f} м)",
                        threshold=f"{self.cfg.max_credible_speed_kmh:.0f} км/ч"
                    ))

        # --- RULE 2: МЕТАНИЕ (Ping-Pong) ---
        if not is_after_gap and len(history) >= 2 and current.get("point_type") == "live":
            p_prev = history[-2]
            d_ab = haversine_distance_meters(p_prev["lat"], p_prev["lon"], prev["lat"], prev["lon"])
            d_bc = haversine_distance_meters(prev["lat"], prev["lon"], current["lat"], current["lon"])
            d_ac = haversine_distance_meters(p_prev["lat"], p_prev["lon"], current["lat"], current["lon"])
            time_total = float(current.get("edit_date", 0) - p_prev.get("edit_date", 0))
            if time_total <= 0:
                time_total = (current.get("received_at", 0) - p_prev.get("received_at", 0)) / 1000.0

            if (d_ab >= self.cfg.ping_pong_min_dist_m and
                d_bc >= self.cfg.ping_pong_min_dist_m and
                d_ac <= 350.0 and
                time_total <= self.cfg.ping_pong_max_time_s * 2):
                flags.append(AnomalyFlag(
                    rule_id="ping_pong_jitter",
                    rule_name="Метание координат (Bounce)",
                    weight=35,
                    explanation=f"Отскок на {d_ab:.0f} м и возврат в исходную область за {time_total:.0f} с. Характерно для ложной базовой станции или Wi-Fi.",
                    observed_value=f"Отскок {d_ab:.0f} м",
                    threshold=f"< {self.cfg.ping_pong_min_dist_m:.0f} м"
                ))

        # --- RULE 3: НЕСОГЛАСОВАННОСТЬ HEADING И ДВИЖЕНИЯ ---
        curr_heading = current.get("heading")
        if (not is_after_gap and prev and curr_heading is not None and bearing_deg is not None and
            speed_kmh >= self.cfg.heading_min_speed_kmh and distance_m >= 25.0):
            diff = angular_difference_deg(float(curr_heading), bearing_deg)
            if diff > self.cfg.heading_diff_threshold_deg:
                flags.append(AnomalyFlag(
                    rule_id="heading_mismatch",
                    rule_name="Несогласованность курса и вектора",
                    weight=20,
                    explanation=f"Заявленный курс компаса ({curr_heading}°) расходится с вектором движения ({bearing_deg:.0f}°) на {diff:.0f}° при скорости {speed_kmh:.1f} км/ч.",
                    observed_value=f"Расхождение {diff:.0f}°",
                    threshold=f"{self.cfg.heading_diff_threshold_deg:.0f}°"
                ))

        # --- RULE 4: НЕРЕАЛЬНАЯ ГЛАДКОСТЬ (Требует выборку >= 30 точек и триаду признаков) ---
        if len(history) >= self.cfg.smoothness_min_points and current.get("point_type") == "live":
            pts = history[-(self.cfg.smoothness_min_points - 1):] + [current]
            speeds = []
            bearings = []
            accs = []
            total_d = 0.0

            for i in range(1, len(pts)):
                d = haversine_distance_meters(pts[i-1]["lat"], pts[i-1]["lon"], pts[i]["lat"], pts[i]["lon"])
                total_d += d
                dt = float(pts[i].get("edit_date", 0) - pts[i-1].get("edit_date", 0))
                if dt <= 0:
                    dt = max((pts[i].get("received_at", 0) - pts[i-1].get("received_at", 0)) / 1000.0, 1.0)
                speeds.append((d / dt) * 3.6)
                if d > 3.0:
                    bearings.append(calculate_bearing_deg(pts[i-1]["lat"], pts[i-1]["lon"], pts[i]["lat"], pts[i]["lon"]))
                if pts[i].get("accuracy") is not None:
                    accs.append(float(pts[i]["accuracy"]))

            if total_d > 500.0 and len(speeds) >= 20 and len(bearings) >= 20:
                s_std = standard_deviation(speeds)
                b_std = standard_deviation(bearings)
                a_std = standard_deviation(accs) if len(accs) >= 10 else 1.0

                # Триада: нулевой шум скорости + нулевой шум азимута + абсолютно неизменная точность
                # Если на трассе/поезде точность или координаты естественно шумят — аномалия НЕ ставится
                is_pure_synthetic = (
                    s_std < self.cfg.smoothness_max_speed_std and
                    b_std < self.cfg.smoothness_max_bearing_std and
                    a_std < 0.01
                )

                if is_pure_synthetic:
                    flags.append(AnomalyFlag(
                        rule_id="unnatural_smoothness",
                        rule_name="Искусственная прямолинейность",
                        weight=25,
                        explanation=f"На выборке из {len(pts)} точек ({total_d:.0f} м) зафиксирована математически нулевая дисперсия скорости ({s_std:.4f} км/ч), курса ({b_std:.4f}°) и точности ({a_std:.4f} м).",
                        observed_value=f"std_v={s_std:.4f}, std_b={b_std:.4f}",
                        threshold=f"< {self.cfg.smoothness_max_speed_std} / {self.cfg.smoothness_max_bearing_std}"
                    ))

        # --- RULE 5: ПОДОЗРИТЕЛЬНАЯ ТОЧНОСТЬ ---
        curr_acc = current.get("accuracy")
        if curr_acc is not None:
            if 0.0 <= curr_acc < self.cfg.accuracy_min_realistic_m:
                flags.append(AnomalyFlag(
                    rule_id="suspicious_accuracy_low",
                    rule_name="Нереалистично высокая точность",
                    weight=15,
                    explanation=f"Заявленная точность {curr_acc} м нереалистично мала для пользовательского смартфона.",
                    observed_value=f"{curr_acc} м",
                    threshold=f"< {self.cfg.accuracy_min_realistic_m} м"
                ))

            # Фиксированная точность подозрительна ТОЛЬКО при движении (speed_kmh > 5 км/ч).
            # На стоящем телефоне (на столе/парковке) неизменная точность естественна и не штрафуется.
            if speed_kmh >= 5.0 and len(history) >= self.cfg.accuracy_fixed_reps:
                recent_accs = [p.get("accuracy") for p in history[-self.cfg.accuracy_fixed_reps:] if p.get("accuracy") is not None]
                if len(recent_accs) >= self.cfg.accuracy_fixed_reps and all(abs(a - curr_acc) < 0.001 for a in recent_accs):
                    flags.append(AnomalyFlag(
                        rule_id="suspicious_accuracy_fixed",
                        rule_name="Фиксированное значение точности",
                        weight=15,
                        explanation=f"Точность неизменно равна {curr_acc} м {self.cfg.accuracy_fixed_reps + 1} правок подряд при движении.",
                        observed_value=f"{curr_acc} м",
                        threshold=f"{self.cfg.accuracy_fixed_reps} повторов"
                    ))

        # --- RULE 6: ПОДОЗРИТЕЛЬНЫЕ КООРДИНАТЫ (Малое число знаков) ---
        lat_dec = get_decimal_places(current["lat"])
        lon_dec = get_decimal_places(current["lon"])
        if lat_dec < self.cfg.min_decimal_places or lon_dec < self.cfg.min_decimal_places:
            flags.append(AnomalyFlag(
                rule_id="suspicious_precision",
                rule_name="Низкая разрядность координат",
                weight=15,
                explanation=f"Координаты имеют малое число знаков после запятой (широта: {lat_dec}, долгота: {lon_dec}).",
                observed_value=f"{min(lat_dec, lon_dec)} знаков",
                threshold=f"< {self.cfg.min_decimal_places}"
            ))

        # --- RULE 7: СТАТИЧНОСТЬ (Frozen Live) ---
        if len(history) >= self.cfg.frozen_min_edits and current.get("point_type") == "live":
            recent_edits = history[-self.cfg.frozen_min_edits:]
            if all(abs(p["lat"] - current["lat"]) < 1e-7 and abs(p["lon"] - current["lon"]) < 1e-7 for p in recent_edits):
                flags.append(AnomalyFlag(
                    rule_id="frozen_live",
                    rule_name="Статичность при активных правках",
                    weight=20,
                    explanation=f"Координаты неизменны {self.cfg.frozen_min_edits + 1} правок подряд при активном потоке сообщений.",
                    observed_value="0.0 м дрейф",
                    threshold=f"> {self.cfg.frozen_min_edits} правок"
                ))

        # --- RULE 8: ТАЙМИНГ (по секундному edit_date, вес не более 5 баллов) ---
        if len(history) >= 6:
            pts_timing = history[-5:] + [current]
            edit_intervals = [
                pts_timing[i]["edit_date"] - pts_timing[i-1]["edit_date"]
                for i in range(1, len(pts_timing))
            ]
            if len(set(edit_intervals)) == 1 and edit_intervals[0] > 0:
                flags.append(AnomalyFlag(
                    rule_id="unnatural_timing",
                    rule_name="Ритмичный интервал правок",
                    weight=min(self.cfg.timing_max_weight, 5), # Не более 5 баллов
                    explanation=f"Интервал между правками edit_date в Telegram составляет ровно {edit_intervals[0]} с. На интервалы влияют сеть и фоновый режим iOS/Android, поэтому вес признака минимальный.",
                    observed_value=f"Шаг {edit_intervals[0]} с",
                    threshold="Идентичный шаг правок"
                ))

        # --- RULE 10: ПОДМЕНА ТИПА СООБЩЕНИЯ (Разрыв непрерывности) ---
        if prev and (prev.get("point_type") == "live" and current.get("point_type") == "static" or
                     (current.get("point_type") == "static" and distance_m >= self.cfg.type_discontinuity_dist_m)):
            flags.append(AnomalyFlag(
                rule_id="type_discontinuity",
                rule_name="Разрыв непрерывности: статический пин",
                weight=25,
                explanation=f"Получена статическая геометка (выбранное место) со смещением {distance_m:.0f} м вместо live-потока.",
                observed_value=f"static, {distance_m:.0f} м",
                threshold="статичная геометка"
            ))

        # --- RULE 11: ПЕРЕЗАПУСК ТРАНСЛЯЦИИ ---
        if not is_after_gap and prev and current.get("point_type") == "live" and current.get("edit_number") == 1 and distance_m > 1000.0:
            flags.append(AnomalyFlag(
                rule_id="session_restart_gap",
                rule_name="Перезапуск трансляции с разрывом",
                weight=20,
                explanation=f"Новая сессия live начата на удалении {distance_m:.0f} м от точки завершения предыдущей.",
                observed_value=f"Разрыв {distance_m:.0f} м",
                threshold="> 1000 м"
            ))

        # --- RULE 12: НЕВОЗМОЖНОЕ МЕСТО ---
        if self.cfg.check_impossible_location:
            is_null = abs(current["lat"]) < 0.001 and abs(current["lon"]) < 0.001
            is_oob = abs(current["lat"]) > 90.0 or abs(current["lon"]) > 180.0
            if is_null or is_oob:
                flags.append(AnomalyFlag(
                    rule_id="impossible_location",
                    rule_name="Недопустимые координаты",
                    weight=50,
                    explanation=f"Координаты ({current['lat']}, {current['lon']}) выходят за пределы земного шара или указывают на Null Island.",
                    observed_value=f"{current['lat']}, {current['lon']}",
                    threshold="Null Island / Range"
                ))

        def get_flag_rule_id(flag_obj):
            if isinstance(flag_obj, dict):
                return flag_obj.get("rule_id", "")
            return getattr(flag_obj, "rule_id", "")

        def get_flag_weight(flag_obj):
            if isinstance(flag_obj, dict):
                return flag_obj.get("weight", 0)
            return getattr(flag_obj, "weight", 0)

        # Подсчёт баллов скользящего окна с затуханием (decay) и ограничением вклада прошлого
        # Чтобы слабые повторяющиеся признаки из прошлых точек не раздували статус до "найдены аномалии"
        recent_pts = history[-5:]
        decay_factors = [0.05, 0.08, 0.12, 0.18, 0.25]
        offset = len(decay_factors) - len(recent_pts)
        past_weighted_sum = 0.0
        for idx, p in enumerate(recent_pts):
            w = sum(get_flag_weight(f) for f in p.get("anomaly_flags", []))
            factor = decay_factors[offset + idx]
            past_weighted_sum += w * factor

        # Вклад прошлых точек ограничен 20 баллами, чтобы без текущих флагов статус не взлетал
        recent_weight = min(20.0, past_weighted_sum)
        curr_weight = sum(f.weight for f in flags)
        total_score = min(100, round(curr_weight + recent_weight))

        # СТРОГИЕ ФОРМУЛИРОВКИ СОГЛАСНО ТРЕБОВАНИЯМ:
        # 0-19: "аномалий не найдено"
        # 20-49: "есть подозрительные признаки: <причины>"
        # 50+: "найдены аномалии: <причины>"
        # Правило unnatural_timing само по себе (вес <= 5) гарантированно оставляет статус в "аномалий не найдено"
        non_timing_weights = [f.weight for f in flags if f.rule_id != "unnatural_timing" and f.weight > 0]
        has_only_timing_or_zero = len(non_timing_weights) == 0 and all(
            all(get_flag_rule_id(f) == "unnatural_timing" or get_flag_weight(f) == 0 for f in p.get("anomaly_flags", []))
            for p in recent_pts
        )

        if total_score >= 50 and not has_only_timing_or_zero:
            overall_status = "flagged"
            reasons = ", ".join(f.rule_name for f in flags if f.weight > 0) or "комплексные отклонения"
            status_text = f"найдены аномалии: {reasons}"
        elif total_score >= 20 and not has_only_timing_or_zero:
            overall_status = "suspicious"
            reasons = ", ".join(f.rule_name for f in flags if f.weight > 0) or "нерегулярности"
            status_text = f"есть подозрительные признаки: {reasons}"
        else:
            overall_status = "clean"
            status_text = "аномалий не найдено"

        return PointEvaluation(
            distance_from_prev_m=distance_m,
            speed_kmh=speed_kmh,
            bearing_deg=bearing_deg,
            flags=flags,
            score=total_score,
            overall_status=overall_status,
            status_text=status_text,
            is_flight=is_flight,
            is_after_gap=is_after_gap
        )
