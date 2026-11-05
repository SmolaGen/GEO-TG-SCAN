"""
Telegram MTProto client wrapper using Telethon.
Features:
- Exponential backoff auto-reconnect and FloodWait handling
- Session files in data/ directory with 0600 permissions
- Server-side geo filter (InputMessagesFilterGeo) for frugal message scanning
- Data gap detection and recording between disconnections
- Sensitive credentials (api_hash, session, phone) are masked and never logged
"""

import os
import time
import asyncio
from typing import Optional, Callable, Dict, Any, List

try:
    from telethon import TelegramClient, events
    from telethon.tl.types import (
        MessageMediaGeoLive,
        MessageMediaGeo,
        GeoPoint as TLGeoPoint,
        InputMessagesFilterGeo
    )
    from telethon.errors import (
        SessionPasswordNeededError,
        PhoneCodeInvalidError,
        PhoneCodeExpiredError,
        FloodWaitError,
        ConnectionError,
    )
except ImportError:
    TelegramClient = None
    events = None
    MessageMediaGeoLive = None
    MessageMediaGeo = None
    TLGeoPoint = None
    InputMessagesFilterGeo = None
    SessionPasswordNeededError = Exception
    PhoneCodeInvalidError = Exception
    PhoneCodeExpiredError = Exception
    FloodWaitError = Exception
    ConnectionError = Exception

class TelegramService:
    def __init__(self, session_path: str, db=None, config=None):
        self.session_path = session_path
        self.db = db
        self.cfg = config
        self.client: Optional[TelegramClient] = None
        self.phone: Optional[str] = None
        self.phone_code_hash: Optional[str] = None
        self.is_authenticated = False
        self.auth_step = "idle"  # idle | code_required | 2fa_required | authenticated
        self.edit_counters: Dict[str, int] = {}
        self.last_received_timestamps: Dict[str, int] = {}
        self.on_geo_update_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self.disconnect_time: Optional[int] = None
        self._is_reconnecting = False
        self._handlers_registered = False
        self.tracked_live_messages: Dict[str, Dict[str, Any]] = {}

    def _ensure_session_permissions(self):
        full_path = self.session_path if self.session_path.endswith(".session") else f"{self.session_path}.session"
        if os.name == "posix" and os.path.exists(full_path):
            try:
                os.chmod(full_path, 0o600)
            except OSError:
                pass

    async def init_client(self, api_id: int, api_hash: str) -> bool:
        if TelegramClient is None:
            raise RuntimeError("Telethon library is not installed.")

        if self.client and self.client.is_connected():
            await self.client.disconnect()

        session_base = self.session_path.replace(".session", "")
        self.client = TelegramClient(session_base, api_id, api_hash)
        
        # Connect with auto-reconnect enabled
        await self.client.connect()
        self._ensure_session_permissions()

        if await self.client.is_user_authorized():
            self.is_authenticated = True
            self.auth_step = "authenticated"
            self._register_event_handlers()
            # Resync any previously known tracked messages
            asyncio.create_task(self.resync_tracked_messages())
            return True
        return False

    async def connect_with_retry(self, max_retries: int = 5):
        """Exponential backoff reconnect loop"""
        if not self.client or self._is_reconnecting:
            return
        self._is_reconnecting = True
        delay = 1.0

        for attempt in range(max_retries):
            try:
                if not self.client.is_connected():
                    await self.client.connect()
                if await self.client.is_user_authorized():
                    self.is_authenticated = True
                    self._register_event_handlers()
                    # Check for data gaps after reconnect and resync tracked messages
                    now_ts = int(time.time())
                    if self.disconnect_time and self.db:
                        for contact_id, last_ts in self.last_received_timestamps.items():
                            if now_ts - last_ts > 60:
                                self.db.insert_gap(contact_id, last_ts, now_ts, "reconnect_gap")
                        self.disconnect_time = None
                    asyncio.create_task(self.resync_tracked_messages())
                    self._is_reconnecting = False
                    return
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds)
            except Exception:
                await asyncio.sleep(delay)
                delay = min(60.0, delay * 2.0)

        self._is_reconnecting = False

    async def send_code(self, phone: str) -> Dict[str, Any]:
        if not self.client:
            raise RuntimeError("Telegram client is not initialized.")
        self.phone = phone
        try:
            res = await self.client.send_code_request(phone)
            self.phone_code_hash = res.phone_code_hash
            self.auth_step = "code_required"
            return {"status": "code_sent"}
        except FloodWaitError as e:
            return {"status": "error", "error": f"FloodWait: подождите {e.seconds} секунд."}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def submit_code(self, code: str) -> Dict[str, Any]:
        if not self.client or not self.phone:
            raise RuntimeError("Phone verification not initiated.")
        try:
            await self.client.sign_in(self.phone, code, phone_code_hash=self.phone_code_hash)
            self.is_authenticated = True
            self.auth_step = "authenticated"
            self._register_event_handlers()
            self._ensure_session_permissions()
            return {"status": "authenticated"}
        except SessionPasswordNeededError:
            self.auth_step = "2fa_required"
            return {"status": "2fa_required", "message": "Требуется пароль двухфакторной аутентификации."}
        except PhoneCodeInvalidError:
            return {"status": "error", "error": "Неверный код подтверждения."}
        except PhoneCodeExpiredError:
            return {"status": "error", "error": "Срок действия кода истёк."}
        except FloodWaitError as e:
            return {"status": "error", "error": f"FloodWait: подождите {e.seconds} секунд."}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def submit_2fa(self, password: str) -> Dict[str, Any]:
        if not self.client:
            raise RuntimeError("Client not connected.")
        try:
            await self.client.sign_in(password=password)
            self.is_authenticated = True
            self.auth_step = "authenticated"
            self._register_event_handlers()
            self._ensure_session_permissions()
            return {"status": "authenticated"}
        except Exception as e:
            return {"status": "error", "error": f"Ошибка 2FA: {str(e)}"}

    async def logout(self):
        if self.client and self.client.is_connected():
            await self.client.log_out()
        self.is_authenticated = False
        self.auth_step = "idle"
        full_path = self.session_path if self.session_path.endswith(".session") else f"{self.session_path}.session"
        if os.path.exists(full_path):
            try:
                os.remove(full_path)
            except OSError:
                pass

    async def resync_tracked_messages(self):
        """
        Re-read current state of tracked live messages after restart or disconnect,
        and log intervals into data_gaps.
        """
        if not self.client or not self.is_authenticated:
            return
        now_ts = int(time.time())
        for key, info in list(self.tracked_live_messages.items()):
            try:
                chat_id = info["chat_id"]
                msg_id = info["message_id"]
                contact_id = info["contact_id"]
                msg = await self.client.get_messages(int(chat_id), ids=msg_id)
                if msg and msg.media:
                    edit_time = int(msg.edit_date.timestamp()) if msg.edit_date else int(msg.date.timestamp())
                    last_known_ts = self.last_received_timestamps.get(contact_id)
                    if last_known_ts and (now_ts - last_known_ts > 60):
                        if self.db:
                            self.db.insert_gap(contact_id, last_known_ts, now_ts, "reconnect_sync_gap")
                    self.last_received_timestamps[contact_id] = edit_time
                    
                    # Fetch latest edit_number from DB to avoid resetting to 1 on reconnect
                    prev_points = self.db.get_recent_points_for_evaluation(contact_id, limit=1) if self.db else []
                    last_num = prev_points[0].get("edit_number", 0) if prev_points else 0
                    current_num = last_num + 1
                    self.edit_counters[key] = current_num

                    # Trigger geo update with latest point
                    geo = getattr(msg.media, "geo", None)
                    if geo:
                        pt = {
                            "contact_id": contact_id,
                            "contact_name": info.get("contact_name", f"User_{contact_id}"),
                            "chat_id": str(chat_id),
                            "message_id": msg.id,
                            "point_type": "live" if isinstance(msg.media, MessageMediaGeoLive) else "static",
                            "lat": geo.lat,
                            "lon": geo.long,
                            "accuracy": getattr(geo, "accuracy_radius", None),
                            "heading": getattr(msg.media, "heading", None),
                            "live_period": getattr(msg.media, "period", None),
                            "edit_date": edit_time,
                            "received_at": int(time.time() * 1000),
                            "edit_number": current_num,
                            "is_after_gap": True
                        }
                        if self.on_geo_update_callback:
                            self.on_geo_update_callback(pt)
            except Exception as e:
                print(f"[TelegramService] Error resyncing message {key}: {e}")

    async def scan_dialogs_for_geolocations(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Scans for live locations using InputMessagesFilterGeo.
        If filter yields no results or is unavailable, falls back to direct dialog iteration with limits.
        Prioritizes live broadcasts (MessageMediaGeoLive).
        """
        if not self.client or not self.is_authenticated:
            if self.db:
                return self.db.get_cached_streams()
            return []

        if not force_refresh and self.db:
            cached = self.db.get_cached_streams()
            if cached:
                return cached

        dialog_limit = getattr(self.cfg, "scan_dialog_limit", 40) if self.cfg else 40
        msg_limit = getattr(self.cfg, "scan_msg_limit", 25) if self.cfg else 25
        delay_s = getattr(self.cfg, "scan_delay_s", 0.25) if self.cfg else 0.25

        found_streams = []
        seen_contacts = set()

        try:
            # 1. Primary search: iterate dialogs using server-side geo filter
            async for dialog in self.client.iter_dialogs(limit=dialog_limit):
                if not dialog.is_user:
                    continue

                entity = dialog.entity
                first_name = getattr(entity, 'first_name', '') or ''
                last_name = getattr(entity, 'last_name', '') or ''
                contact_name = f"{first_name} {last_name}".strip()
                if not contact_name:
                    contact_name = getattr(entity, 'username', '') or f"User_{dialog.id}"

                contact_id = str(dialog.id)
                stream_for_dialog = None

                # First pass: try with InputMessagesFilterGeo
                try:
                    filter_obj = InputMessagesFilterGeo() if InputMessagesFilterGeo else None
                    async for message in self.client.iter_messages(dialog.id, limit=msg_limit, filter=filter_obj):
                        if not message.media:
                            continue
                        media = message.media
                        if isinstance(media, MessageMediaGeoLive):
                            geo: TLGeoPoint = media.geo
                            period = getattr(media, "period", 0)
                            is_indefinite = period == 0x7FFFFFFF or period > 86400 * 30
                            edit_time = int(message.edit_date.timestamp()) if message.edit_date else int(message.date.timestamp())
                            stream_for_dialog = {
                                "contact_id": contact_id,
                                "contact_name": contact_name,
                                "chat_id": contact_id,
                                "message_id": message.id,
                                "point_type": "live",
                                "is_live": True,
                                "is_indefinite": is_indefinite,
                                "live_period": period,
                                "lat": geo.lat,
                                "lon": geo.long,
                                "accuracy": getattr(geo, "accuracy_radius", None),
                                "heading": getattr(media, "heading", None),
                                "edit_date": edit_time,
                                "received_at": int(time.time() * 1000),
                            }
                            break  # Found active live stream for this contact
                        elif isinstance(media, MessageMediaGeo) and not stream_for_dialog:
                            geo: TLGeoPoint = media.geo
                            date_time = int(message.date.timestamp())
                            stream_for_dialog = {
                                "contact_id": contact_id,
                                "contact_name": contact_name,
                                "chat_id": contact_id,
                                "message_id": message.id,
                                "point_type": "static",
                                "is_live": False,
                                "is_indefinite": False,
                                "live_period": None,
                                "lat": geo.lat,
                                "lon": geo.long,
                                "accuracy": getattr(geo, "accuracy_radius", None),
                                "heading": None,
                                "edit_date": date_time,
                                "received_at": int(time.time() * 1000),
                            }
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds)
                except Exception:
                    # Fallback to direct message scan without filter
                    try:
                        async for message in self.client.iter_messages(dialog.id, limit=min(msg_limit, 15)):
                            if not message.media:
                                continue
                            media = message.media
                            if isinstance(media, MessageMediaGeoLive):
                                geo: TLGeoPoint = media.geo
                                period = getattr(media, "period", 0)
                                is_indefinite = period == 0x7FFFFFFF or period > 86400 * 30
                                edit_time = int(message.edit_date.timestamp()) if message.edit_date else int(message.date.timestamp())
                                stream_for_dialog = {
                                    "contact_id": contact_id,
                                    "contact_name": contact_name,
                                    "chat_id": contact_id,
                                    "message_id": message.id,
                                    "point_type": "live",
                                    "is_live": True,
                                    "is_indefinite": is_indefinite,
                                    "live_period": period,
                                    "lat": geo.lat,
                                    "lon": geo.long,
                                    "accuracy": getattr(geo, "accuracy_radius", None),
                                    "heading": getattr(media, "heading", None),
                                    "edit_date": edit_time,
                                    "received_at": int(time.time() * 1000),
                                }
                                break
                    except Exception:
                        pass

                if stream_for_dialog and contact_id not in seen_contacts:
                    seen_contacts.add(contact_id)
                    found_streams.append(stream_for_dialog)
                    if stream_for_dialog["is_live"]:
                        key = f"{contact_id}_{stream_for_dialog['message_id']}"
                        self.tracked_live_messages[key] = stream_for_dialog

                await asyncio.sleep(delay_s)

            # Cache found streams in DB
            if self.db and found_streams:
                self.db.cache_streams(found_streams)

        except Exception as e:
            print(f"[TelegramService] Scan error: {e}")

        return found_streams

    def _register_event_handlers(self):
        if not self.client or self._handlers_registered:
            return
        self._handlers_registered = True

        @self.client.on(events.MessageEdited())
        async def on_message_edited(event):
            await self._handle_geo_event(event, is_edit=True)

        @self.client.on(events.NewMessage())
        async def on_new_message(event):
            await self._handle_geo_event(event, is_edit=False)

    async def _handle_geo_event(self, event, is_edit: bool = False):
        # Only track 1-on-1 private chats and incoming messages from the contact (not own outgoing messages)
        if not getattr(event, "is_private", True):
            return
        if getattr(event, "out", False):
            return

        message = event.message
        if not message or not message.media:
            return

        media = message.media
        is_live = isinstance(media, MessageMediaGeoLive)
        is_static = isinstance(media, MessageMediaGeo)

        if not (is_live or is_static):
            return

        geo = getattr(media, "geo", None)
        if not geo:
            return

        sender = await event.get_sender()
        # Contact ID is sender.id for private messages
        contact_id = str(sender.id if sender and hasattr(sender, "id") else event.chat_id)
        first_name = getattr(sender, 'first_name', '') or ''
        last_name = getattr(sender, 'last_name', '') or ''
        contact_name = f"{first_name} {last_name}".strip()
        if not contact_name:
            contact_name = getattr(sender, "username", "") or f"User_{contact_id}"

        key = f"{contact_id}_{message.id}"
        if key not in self.edit_counters:
            # Check DB for previous edit number to avoid restart reset to 1
            prev = self.db.get_recent_points_for_evaluation(contact_id, limit=1) if self.db else []
            self.edit_counters[key] = prev[0].get("edit_number", 0) if prev else 0
        self.edit_counters[key] += 1
        edit_num = self.edit_counters[key]

        edit_time = int(message.edit_date.timestamp()) if message.edit_date else int(message.date.timestamp())
        now_ms = int(time.time() * 1000)

        # Check for gap between previous reception for this contact
        last_ts = self.last_received_timestamps.get(contact_id)
        is_after_gap = False
        if last_ts and (edit_time - last_ts > 600): # >10 min gap
            is_after_gap = True
            if self.db:
                self.db.insert_gap(contact_id, last_ts, edit_time, "prolonged_silence_gap")

        self.last_received_timestamps[contact_id] = edit_time

        point_data = {
            "contact_id": contact_id,
            "contact_name": contact_name,
            "chat_id": str(event.chat_id),
            "message_id": message.id,
            "point_type": "live" if is_live else "static",
            "lat": geo.lat,
            "lon": geo.long,
            "accuracy": getattr(geo, "accuracy_radius", None),
            "heading": getattr(media, "heading", None),
            "live_period": getattr(media, "period", None) if is_live else None,
            "edit_date": edit_time,
            "received_at": now_ms,
            "edit_number": edit_num,
            "is_after_gap": is_after_gap
        }

        if self.on_geo_update_callback:
            self.on_geo_update_callback(point_data)
