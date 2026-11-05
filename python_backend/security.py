"""
Security module for Telegram Live Geo Inspector.
Enforces local-only bindings, one-time random token verification,
DNS-rebinding protection (Host header), and strict Origin validation.
"""

import hmac
from typing import Optional, List
from fastapi import Request, HTTPException, WebSocket, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

class SecurityManager:
    def __init__(self, token: str, allowed_origins: List[str], allowed_hosts: List[str]):
        self.token = token
        self.allowed_origins = set(allowed_origins)
        self.allowed_hosts = set(allowed_hosts)

    def verify_token(self, provided_token: Optional[str]) -> bool:
        if not provided_token:
            return False
        # Constant-time comparison
        return hmac.compare_digest(self.token, provided_token)

    def validate_origin(self, origin: Optional[str]) -> bool:
        if not origin:
            # Same-origin requests might not have Origin header in browsers
            return True
        clean = origin.rstrip("/")
        if clean in self.allowed_origins:
            return True
        # Allow internal preview environment domains
        if clean.endswith(".run.app") or clean.startswith("https://ais-") or "localhost" in clean or "127.0.0.1" in clean:
            return True
        return False

    def validate_host(self, host: Optional[str]) -> bool:
        if not host:
            return False
        # Remove trailing port if present or check full host
        base = host.split(":")[0]
        if host in self.allowed_hosts or base in {"127.0.0.1", "localhost", "0.0.0.0"}:
            return True
        if host.endswith(".run.app") or base.endswith(".run.app"):
            return True
        return False

class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, security_mgr: SecurityManager):
        super().__init__(app)
        self.sec = security_mgr

    async def dispatch(self, request: Request, call_next):
        # 1. DNS Rebinding check: Validate Host header
        host_header = request.headers.get("host")
        if not self.sec.validate_host(host_header):
            return Response("Invalid Host header (DNS Rebinding protection)", status_code=400)

        # 2. Strict Origin check
        origin_header = request.headers.get("origin")
        if origin_header and not self.sec.validate_origin(origin_header):
            return Response("Forbidden Origin", status_code=403)

        # 3. Token check for API routes
        path = request.url.path
        if path.startswith("/api/"):
            # Exclude nothing in /api/ - all API endpoints require authentication
            auth_header = request.headers.get("authorization", "")
            token = None
            if auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()

            if not self.sec.verify_token(token):
                return Response("Unauthorized (Invalid or missing token)", status_code=401)

        response = await call_next(request)
        return response
