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
            # Same-origin GET/fetch may omit Origin in browsers
            return True
        from urllib.parse import urlparse
        try:
            parsed = urlparse(origin)
            # Only http or https schemes allowed
            if parsed.scheme not in {"http", "https"}:
                return False
            # Strict hostname check: strictly 127.0.0.1 or localhost (no evil subdomains like localhost.evil.com)
            if parsed.hostname not in {"127.0.0.1", "localhost"}:
                return False
            # If port specified, must match allowed ports
            if parsed.port and parsed.port not in {8000, 3000}:
                return False
            return True
        except Exception:
            return False

    def validate_host(self, host: Optional[str]) -> bool:
        if not host:
            return False
        # Strict Host check against DNS rebinding
        # Format: hostname or hostname:port
        parts = host.split(":")
        hostname = parts[0].lower().strip()
        if hostname not in {"127.0.0.1", "localhost"}:
            return False
        if len(parts) == 2:
            try:
                port = int(parts[1])
                if port not in {8000, 3000}:
                    return False
            except ValueError:
                return False
        elif len(parts) > 2:
            return False
        return True

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
