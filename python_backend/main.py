"""
FastAPI Local Backend for Telegram Live Geo Inspector.
Single Process, Single Command: strictly bound to 127.0.0.1:8000.
Serves static frontend (HTML + vanilla JS + Leaflet CDN).
Enforces DNS-rebinding protection, Origin validation, Bearer token auth,
and backend-authoritative anomaly detection.
"""

import os
import json
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query, Body, Request
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import Response, JSONResponse, FileResponse, HTMLResponse
    from pydantic import BaseModel
except ImportError:
    FastAPI = None
    WebSocket = None
    WebSocketDisconnect = Exception
    HTTPException = Exception
    BaseModel = object

try:
    from .config import config, DetectorConfig
    from .db import Database
    from .detector import AnomalyDetector
    from .tg_client import TelegramService
    from .security import SecurityManager, SecurityMiddleware
    from .scenarios import SCENARIO_GENERATORS
except (ImportError, ValueError):
    from config import config, DetectorConfig
    from db import Database
    from detector import AnomalyDetector
    from tg_client import TelegramService
    from security import SecurityManager, SecurityMiddleware
    from scenarios import SCENARIO_GENERATORS

db = Database(config.db_path)
detector = AnomalyDetector(config.detector)
tg_service = TelegramService(config.session_file.replace(".session", ""), db=db, config=config)
security_mgr = SecurityManager(
    token=config.auth_token,
    allowed_origins=config.allowed_origins,
    allowed_hosts=config.allowed_hosts
)

connected_clients: List[WebSocket] = []

def on_telegram_geo_point(point_dict: Dict[str, Any]):
    """
    Invoked when Telegram MTProto receives an edit or new geo message.
    Evaluates point using single source of truth AnomalyDetector, saves to DB, and broadcasts.
    """
    contact_id = point_dict["contact_id"]
    recent_history = db.get_recent_points_for_evaluation(contact_id, limit=35)
    
    evaluation = detector.evaluate_point(point_dict, recent_history)
    db.insert_point(point_dict, evaluation)

    broadcast_payload = {
        "type": "geo_point",
        "point": {
            **point_dict,
            "distance_from_prev": evaluation.distance_from_prev_m,
            "speed_kmh": evaluation.speed_kmh,
            "bearing_deg": evaluation.bearing_deg,
            "anomaly_score": evaluation.score,
            "anomaly_flags": [
                {
                    "rule_id": f.rule_id,
                    "rule_name": f.rule_name,
                    "weight": f.weight,
                    "explanation": f.explanation,
                    "observed_value": f.observed_value,
                    "threshold": f.threshold
                }
                for f in evaluation.flags
            ],
            "overall_status": evaluation.overall_status,
            "status_text": evaluation.status_text,
            "is_flight": evaluation.is_flight,
            "is_after_gap": evaluation.is_after_gap
        }
    }

    message_str = json.dumps(broadcast_payload, ensure_ascii=False)
    for ws in list(connected_clients):
        try:
            asyncio.create_task(ws.send_text(message_str))
        except Exception:
            pass

tg_service.on_geo_update_callback = on_telegram_geo_point

if FastAPI is not None:
    app = FastAPI(
        title="Telegram Live Geo Inspector",
        description="Local-only daemon for monitoring Telegram live locations with anomaly detection."
    )

    # Mount security middleware (Host check, Origin check, Bearer token on /api/)
    app.add_middleware(SecurityMiddleware, security_mgr=security_mgr)

    # Static files directory
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    os.makedirs(static_dir, exist_ok=True)

    class AuthStartRequest(BaseModel):
        api_id: int
        api_hash: str
        phone: str

    class AuthCodeRequest(BaseModel):
        code: str

    class Auth2FARequest(BaseModel):
        password: str

    class ConfigUpdateRequest(BaseModel):
        max_credible_speed_kmh: Optional[float] = None
        flight_speed_threshold_kmh: Optional[float] = None
        flight_min_points: Optional[int] = None
        teleport_min_distance_m: Optional[float] = None
        ping_pong_min_dist_m: Optional[float] = None
        heading_diff_threshold_deg: Optional[float] = None
        smoothness_min_points: Optional[int] = None
        accuracy_min_realistic_m: Optional[float] = None
        min_decimal_places: Optional[int] = None
        frozen_min_edits: Optional[int] = None
        timing_max_weight: Optional[int] = None

    @app.on_event("startup")
    async def startup_event():
        url = f"http://127.0.0.1:{config.port}/#token={config.auth_token}"
        print("\n" + "=" * 65)
        print("  Telegram Live Geo Inspector запущен локально")
        print("  Адрес для входа в браузере (токен в хэше URL):")
        print(f"  --> {url}")
        print("=" * 65 + "\n")

    @app.get("/")
    async def serve_index():
        index_file = os.path.join(static_dir, "index.html")
        if os.path.exists(index_file):
            with open(index_file, "r", encoding="utf-8") as f:
                content = f.read()
            # Inject token bootstrap script safely into head
            token_script = f'<script>window.__BOOTSTRAP_AUTH_TOKEN__ = "{config.auth_token}";</script>'
            content = content.replace("</head>", f"{token_script}\n</head>")
            return HTMLResponse(content)
        return Response("index.html not found in static dir", status_code=404)

    # Mount static assets
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/api/status")
    async def get_app_status():
        return {
            "authenticated": tg_service.is_authenticated,
            "auth_step": tg_service.auth_step,
            "connected_ws_clients": len(connected_clients),
            "token_verified": True,
            "disclaimer": "Telegram передаёт только заявленные отправителем данные. Приложение НЕ может доказать подмену и НЕ может доказать подлинность. Любая аномалия — это предупреждение эвристики."
        }

    @app.post("/api/auth/start")
    async def start_auth(req: AuthStartRequest):
        try:
            await tg_service.init_client(req.api_id, req.api_hash)
            res = await tg_service.send_code(req.phone)
            return res
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/auth/code")
    async def submit_code(req: AuthCodeRequest):
        try:
            res = await tg_service.submit_code(req.code)
            return res
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/auth/2fa")
    async def submit_2fa(req: Auth2FARequest):
        try:
            res = await tg_service.submit_2fa(req.password)
            return res
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/auth/logout")
    async def logout():
        await tg_service.logout()
        return {"status": "logged_out"}

    @app.get("/api/broadcasts/scan")
    async def scan_broadcasts(refresh: bool = False):
        if not tg_service.is_authenticated:
            # Fall back to SQLite cached streams
            streams = db.get_cached_streams()
            return {"streams": streams, "cached": True}
        streams = await tg_service.scan_dialogs_for_geolocations(force_refresh=refresh)
        return {"streams": streams, "cached": False}

    @app.get("/api/history")
    async def get_history(
        contact_id: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        only_anomalies: bool = False
    ):
        points = db.get_contact_history(contact_id, start_ts, end_ts, only_anomalies)
        gaps = db.get_gaps(contact_id)
        return {"points": points, "gaps": gaps}

    @app.get("/api/gaps")
    async def get_gaps(contact_id: str):
        gaps = db.get_gaps(contact_id)
        return {"gaps": gaps}

    @app.delete("/api/history")
    async def delete_history(contact_id: str):
        deleted = db.delete_contact_history(contact_id)
        return {"deleted_points": deleted, "status": "deleted"}

    @app.get("/api/config")
    async def get_config():
        cfg = config.detector
        return {
            "max_credible_speed_kmh": cfg.max_credible_speed_kmh,
            "flight_speed_threshold_kmh": cfg.flight_speed_threshold_kmh,
            "flight_min_points": cfg.flight_min_points,
            "teleport_min_distance_m": cfg.teleport_min_distance_m,
            "ping_pong_min_dist_m": cfg.ping_pong_min_dist_m,
            "heading_diff_threshold_deg": cfg.heading_diff_threshold_deg,
            "smoothness_min_points": cfg.smoothness_min_points,
            "smoothness_max_speed_std": cfg.smoothness_max_speed_std,
            "smoothness_max_bearing_std": cfg.smoothness_max_bearing_std,
            "accuracy_min_realistic_m": cfg.accuracy_min_realistic_m,
            "accuracy_fixed_reps": cfg.accuracy_fixed_reps,
            "min_decimal_places": cfg.min_decimal_places,
            "frozen_min_edits": cfg.frozen_min_edits,
            "timing_max_weight": cfg.timing_max_weight,
            "stale_timeout_s": cfg.stale_timeout_s,
        }

    @app.put("/api/config")
    async def update_config(req: ConfigUpdateRequest):
        cfg = config.detector
        updated = req.dict(exclude_unset=True)
        for k, v in updated.items():
            if hasattr(cfg, k) and v is not None:
                setattr(cfg, k, v)
        detector.cfg = cfg
        # Persist to data/config.json with 0600 permissions
        try:
            config.save_detector_config_to_file()
        except Exception as e:
            print(f"[Config] Failed to save config: {e}")
        return {"status": "updated", "config": await get_config()}

    @app.get("/api/scenarios/list")
    async def list_scenarios():
        return [
            {
                "id": s["id"],
                "title": s["title"],
                "description": s["description"],
                "expected_status": s["expected_status"]
            }
            for s in SCENARIO_GENERATORS.values()
        ]

    @app.post("/api/scenarios/run")
    async def run_scenario(scenario_id: str = Query(..., alias="id")):
        if scenario_id not in SCENARIO_GENERATORS:
            raise HTTPException(status_code=404, detail="Сценарий не найден")
        
        sc_def = SCENARIO_GENERATORS[scenario_id]
        raw_points = sc_def["generator"]()
        
        evaluated_points = []
        for p in raw_points:
            eval_res = detector.evaluate_point(p, evaluated_points)
            point_enriched = {
                **p,
                "distance_from_prev": eval_res.distance_from_prev_m,
                "speed_kmh": eval_res.speed_kmh,
                "bearing_deg": eval_res.bearing_deg,
                "anomaly_score": eval_res.score,
                "anomaly_flags": [
                    {
                        "rule_id": f.rule_id,
                        "rule_name": f.rule_name,
                        "weight": f.weight,
                        "explanation": f.explanation,
                        "observed_value": f.observed_value,
                        "threshold": f.threshold
                    }
                    for f in eval_res.flags
                ],
                "overall_status": eval_res.overall_status,
                "status_text": eval_res.status_text,
                "is_flight": eval_res.is_flight,
                "is_after_gap": eval_res.is_after_gap
            }
            evaluated_points.append(point_enriched)

        # Detect gaps
        gaps = []
        for i in range(1, len(evaluated_points)):
            if evaluated_points[i].get("is_after_gap"):
                gaps.append({
                    "contact_id": evaluated_points[i]["contact_id"],
                    "gap_start_ts": evaluated_points[i-1]["edit_date"],
                    "gap_end_ts": evaluated_points[i]["edit_date"],
                    "reason": "tunnel_signal_loss"
                })

        return {
            "scenario": {
                "id": sc_def["id"],
                "title": sc_def["title"],
                "description": sc_def["description"],
                "expected_status": sc_def["expected_status"]
            },
            "points": evaluated_points,
            "gaps": gaps
        }

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        # 1. Validate Origin header
        origin = websocket.headers.get("origin")
        if origin and not security_mgr.validate_origin(origin):
            await websocket.close(code=1008)
            return

        await websocket.accept()

        # 2. Wait for initial authentication message {"auth": token} within 5s
        # No query string used - prevents token leakage in access logs
        try:
            auth_msg = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
            token = auth_msg.get("auth") if isinstance(auth_msg, dict) else None
            if not security_mgr.verify_token(token):
                await websocket.close(code=1008)
                return
        except Exception:
            try:
                await websocket.close(code=1008)
            except Exception:
                pass
            return

        connected_clients.append(websocket)
        try:
            # Send initial connection ack
            await websocket.send_json({"type": "auth_ok", "authenticated": True})
            while True:
                msg = await websocket.receive_text()
                if msg == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            pass
        finally:
            if websocket in connected_clients:
                connected_clients.remove(websocket)
else:
    app = None
