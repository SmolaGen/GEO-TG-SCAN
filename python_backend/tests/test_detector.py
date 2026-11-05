import unittest
import sys
import os

# Ensure python_backend is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from detector import AnomalyDetector, haversine_distance_meters, calculate_bearing_deg
from config import DetectorConfig
from scenarios import (
    generate_flight,
    generate_sapsan_train,
    generate_highway_drive,
    generate_tunnel_signal_loss,
    generate_normal_walk
)

def make_point(overrides=None):
    base = {
        "contact_id": "123456",
        "contact_name": "Тестовый Контакт",
        "chat_id": "123456",
        "message_id": 100,
        "point_type": "live",
        "lat": 55.751244,
        "lon": 37.618423,
        "accuracy": 10.0,
        "heading": 90,
        "live_period": 2147483647,
        "edit_date": 1700000000,
        "received_at": 1700000000000,
        "edit_number": 1,
        "is_after_gap": False
    }
    if overrides:
        base.update(overrides)
    return base

class TestAnomalyDetector(unittest.TestCase):
    def setUp(self):
        self.detector = AnomalyDetector()

    def test_normal_walk_clean(self):
        points = generate_normal_walk()
        evaluated = []
        for p in points:
            ev = self.detector.evaluate_point(p, evaluated)
            evaluated.append({**p, "speed_kmh": ev.speed_kmh, "anomaly_flags": ev.flags})
            self.assertEqual(ev.overall_status, "clean")
            self.assertEqual(ev.status_text, "аномалий не найдено")

    def test_flight_sustained_clean_no_false_alarm(self):
        """
        Flight test: High speed (>350 km/h) sustained over continuous points
        must be marked as 'возможно, перелёт' with 0 penalty points, leaving status 'clean'.
        """
        flight_points = generate_flight()
        evaluated = []
        flight_flags_found = 0
        for p in flight_points:
            ev = self.detector.evaluate_point(p, evaluated)
            evaluated.append({**p, "speed_kmh": ev.speed_kmh, "anomaly_flags": ev.flags})
            if any(f.rule_id == "possible_flight" for f in ev.flags):
                flight_flags_found += 1
            # Status must remain clean, no teleportation penalty
            self.assertFalse(any(f.rule_id == "teleportation" for f in ev.flags))
            self.assertEqual(ev.overall_status, "clean")
            self.assertEqual(ev.status_text, "аномалий не найдено")
        self.assertGreater(flight_flags_found, 0, "Flight flag must be detected on sustained high speed")

    def test_instant_teleport_leap_always_flagged(self):
        """
        Instant teleportation leap: jumping 55 km in 4 seconds is always an anomaly,
        even if speed is high.
        """
        p1 = make_point({"lat": 55.751244, "lon": 37.618423, "edit_date": 1700000000})
        p2 = make_point({"lat": 56.251244, "lon": 37.618423, "edit_date": 1700000004, "edit_number": 2})
        ev = self.detector.evaluate_point(p2, [p1])
        has_teleport = any(f.rule_id == "teleportation" for f in ev.flags)
        self.assertTrue(has_teleport, "Instant jump on kilometers within seconds must trigger teleportation")
        self.assertIn(ev.overall_status, ["suspicious", "flagged"])

    def test_sapsan_train_clean_no_false_alarm(self):
        """
        Sapsan train: 210 km/h on rail with normal GPS noise must NOT trigger teleportation.
        """
        train_points = generate_sapsan_train()
        evaluated = []
        for p in train_points:
            ev = self.detector.evaluate_point(p, evaluated)
            evaluated.append({**p, "speed_kmh": ev.speed_kmh, "anomaly_flags": ev.flags})
            has_teleport = any(f.rule_id == "teleportation" for f in ev.flags)
            self.assertFalse(has_teleport, "Sapsan speed under 250 km/h must not trigger teleportation")
            self.assertEqual(ev.overall_status, "clean")

    def test_highway_drive_smoothness_no_false_alarm(self):
        """
        Highway M11 drive: 35 points with natural GPS sensor noise (bearing, accuracy, speed).
        Must NOT trigger unnatural_smoothness.
        """
        highway_points = generate_highway_drive()
        evaluated = []
        for p in highway_points:
            ev = self.detector.evaluate_point(p, evaluated)
            evaluated.append({**p, "speed_kmh": ev.speed_kmh, "anomaly_flags": ev.flags})
            has_smoothness = any(f.rule_id == "unnatural_smoothness" for f in ev.flags)
            self.assertFalse(has_smoothness, "Highway driving with natural sensor noise must not trigger unnatural_smoothness")
            self.assertEqual(ev.overall_status, "clean")

    def test_tunnel_signal_loss_gap_resumption(self):
        """
        Tunnel test: 15-minute gap between points.
        Point after tunnel has is_after_gap=True. Must be labeled 'скачок после пробела', 0 penalty.
        """
        tunnel_points = generate_tunnel_signal_loss()
        evaluated = []
        last_ev = None
        for p in tunnel_points:
            ev = self.detector.evaluate_point(p, evaluated)
            evaluated.append({**p, "speed_kmh": ev.speed_kmh, "anomaly_flags": ev.flags})
            last_ev = ev

        self.assertIsNotNone(last_ev)
        has_gap_flag = any(f.rule_id == "data_gap_resumption" for f in last_ev.flags)
        self.assertTrue(has_gap_flag, "Point after gap must have data_gap_resumption flag")
        has_teleport = any(f.rule_id == "teleportation" for f in last_ev.flags)
        self.assertFalse(has_teleport, "Point after gap must not be penalized for speed")
        self.assertEqual(last_ev.overall_status, "clean")

    def test_timing_metronome_rule_capped_at_five_points(self):
        """
        Timing rule: When Telegram edit_date has strict 5s rhythm,
        it assigns at most 5 points and CANNOT elevate status above 'аномалий не найдено'.
        """
        history = []
        base_time = 1700000000
        for i in range(8):
            history.append(make_point({
                "lat": round(55.751234 + i * 0.000051, 6),
                "lon": round(37.618234 + i * 0.000051, 6),
                "accuracy": 8.0 + (i % 4) * 2.0, # natural sensor variation
                "edit_date": base_time + i * 5, # strict 5-second interval
                "edit_number": i + 1
            }))

        p_curr = make_point({
            "lat": round(55.751234 + 8 * 0.000051, 6),
            "lon": round(37.618234 + 8 * 0.000051, 6),
            "accuracy": 14.0,
            "edit_date": base_time + 8 * 5,
            "edit_number": 9
        })

        ev = self.detector.evaluate_point(p_curr, history)
        timing_flag = next((f for f in ev.flags if f.rule_id == "unnatural_timing"), None)
        self.assertIsNotNone(timing_flag)
        self.assertLessEqual(timing_flag.weight, 5, "Timing rule weight must be <= 5 points")
        self.assertEqual(ev.overall_status, "clean")
        self.assertEqual(ev.status_text, "аномалий не найдено")

    def test_bad_reception_within_accuracy_circle_no_false_alarm(self):
        p1 = make_point({"lat": 55.751000, "lon": 37.618000, "accuracy": 150.0, "edit_date": 1700000000})
        p2 = make_point({"lat": 55.752000, "lon": 37.618000, "accuracy": 150.0, "edit_date": 1700000001, "edit_number": 2})
        ev = self.detector.evaluate_point(p2, [p1])
        has_teleport = any(f.rule_id == "teleportation" for f in ev.flags)
        self.assertFalse(has_teleport, "Leap inside accuracy radius sum MUST NOT trigger teleportation")

    def test_frozen_live(self):
        history = []
        base_lat, base_lon = 55.7512345, 37.6184567
        for i in range(5):
            history.append(make_point({
                "lat": base_lat, "lon": base_lon,
                "edit_date": 1700000000 + i * 5,
                "edit_number": i + 1,
            }))
        p_next = make_point({
            "lat": base_lat, "lon": base_lon,
            "edit_date": 1700000030,
            "edit_number": 6,
        })
        ev = self.detector.evaluate_point(p_next, history)
        has_frozen = any(f.rule_id == "frozen_live" for f in ev.flags)
        self.assertTrue(has_frozen)

    def test_phrasing_strictly_negative_constraints(self):
        p = make_point()
        ev = self.detector.evaluate_point(p, [])
        self.assertEqual(ev.status_text, "аномалий не найдено")
        forbidden = ["gps подтверждён", "подмены нет", "подмена доказана", "взлом", "подлинность подтверждена"]
        for phrase in forbidden:
            self.assertNotIn(phrase, ev.status_text.lower())

if __name__ == "__main__":
    unittest.main()
