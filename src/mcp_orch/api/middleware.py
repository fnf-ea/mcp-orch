"""
API 미들웨어

인증, 로깅 등의 미들웨어 구현
"""

import logging
import time
from typing import Callable

from fastapi import Request, Response, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse
from starlette.status import HTTP_204_NO_CONTENT
from starlette.middleware.cors import CORSMiddleware

from ..config import Settings

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    로깅 미들웨어
    
    요청/응답 로깅을 처리합니다.
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """요청 처리"""
        start_time = time.time()
        
        # 요청 로깅
        logger.info(
            f"Request: {request.method} {request.url.path} "
            f"from {request.client.host if request.client else 'unknown'}"
        )
        
        # 요청 처리
        response = await call_next(request)
        
        # 응답 로깅
        process_time = time.time() - start_time
        logger.info(
            f"Response: {response.status_code} "
            f"({process_time:.3f}s)"
        )
        
        # 응답 헤더에 처리 시간 추가
        response.headers["X-Process-Time"] = str(process_time)
        
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    속도 제한 미들웨어
    
    API 호출 속도를 제한합니다.
    """
    
    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.request_counts = {}
        
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """요청 처리"""
        # 클라이언트 IP 가져오기
        client_ip = request.client.host if request.client else "unknown"
        
        # 현재 분 가져오기
        current_minute = int(time.time() / 60)
        
        # 요청 카운트 키
        key = f"{client_ip}:{current_minute}"
        
        # 요청 카운트 증가
        if key not in self.request_counts:
            self.request_counts[key] = 0
        self.request_counts[key] += 1
        
        # 오래된 카운트 정리
        self._cleanup_old_counts(current_minute)
        
        # 속도 제한 확인
        if self.request_counts[key] > self.requests_per_minute:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "retry_after": 60
                },
                headers={
                    "Retry-After": "60"
                }
            )
            
        # 요청 처리
        response = await call_next(request)
        
        # 남은 요청 수 헤더 추가
        remaining = self.requests_per_minute - self.request_counts[key]
        response.headers["X-RateLimit-Limit"] = str(self.requests_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str((current_minute + 1) * 60)
        
        return response
        
    def _cleanup_old_counts(self, current_minute: int):
        """오래된 카운트 정리"""
        # 2분 이상 된 카운트 제거
        old_keys = [
            key for key in self.request_counts
            if int(key.split(":")[1]) < current_minute - 1
        ]
        for key in old_keys:
            del self.request_counts[key]


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """
    JWT 인증 미들웨어
    
    요청 헤더에서 JWT 토큰을 추출하고 검증합니다.
    """
    
    def __init__(self, app):
        super().__init__(app)
        # JWT 기능이 필요할 때만 import
        try:
            from ..utils import verify_jwt_token
            self.verify_jwt_token = verify_jwt_token
        except ImportError:
            logger.warning("JWT utility functions not available. JWT authentication disabled.")
            self.verify_jwt_token = None
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """요청 처리"""
        # JWT 검증 기능이 없으면 인증 없이 통과
        if self.verify_jwt_token is None:
            logger.warning("JWT authentication skipped - verify_jwt_token not available")
            return await call_next(request)
            
        # 인증 헤더 가져오기
        auth_header = request.headers.get("Authorization")
        
        # 인증 헤더가 없으면 401 Unauthorized 반환
        if not auth_header:
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        # Bearer 토큰 추출
        token = auth_header.split(" ")[1]
        
        # 토큰 검증
        user_info = self.verify_jwt_token(token)
        
        # 사용자 정보를 요청 객체에 추가
        request.state.user = user_info
        
        # 요청 처리
        response = await call_next(request)
        
        return response


class SuppressNoResponseReturnedMiddleware:
    """
    SSE 연결 해제 시 발생하는 오류 처리 미들웨어
    
    SSE 엔드포인트에서 클라이언트가 연결을 해제할 때 발생하는
    AssertionError와 RuntimeError를 처리합니다.
    
    BaseHTTPMiddleware 대신 직접 ASGI 미들웨어로 구현하여
    SSE 스트리밍과의 충돌을 방지합니다.
    """
    
    def __init__(self, app):
        self.app = app
    
    async def __call__(self, scope, receive, send):
        """ASGI 호출 처리"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        # SSE 엔드포인트인지 확인
        path = scope.get("path", "")
        is_sse = "/sse" in path or path.endswith("/messages")
        
        if not is_sse:
            # SSE가 아닌 경우 정상 처리
            await self.app(scope, receive, send)
            return
        
        # SSE 엔드포인트는 에러 처리 래핑
        async def wrapped_send(message):
            try:
                await send(message)
            except (RuntimeError, ConnectionError) as exc:
                # 연결이 끊긴 경우 조용히 처리
                logger.debug(f"Client disconnected during SSE stream: {exc}")
                return
        
        try:
            await self.app(scope, receive, wrapped_send)
        except (RuntimeError, AssertionError, ConnectionError) as exc:
            # 스트리밍 중 연결 끊김 처리
            if 'http.response.body' in str(exc) or 'No response returned' in str(exc):
                logger.debug(f"SSE stream interrupted: {exc}")
                # 이미 응답이 시작된 경우 아무것도 하지 않음
                return
            raise


class SSECompatibleCORSMiddleware:
    """
    SSE 호환 CORS 미들웨어
    
    SSE 엔드포인트에 대해서는 간단한 CORS 헤더만 추가하고,
    일반 엔드포인트에 대해서는 표준 CORS 미들웨어를 사용합니다.
    """
    
    def __init__(self, app, **cors_options):
        self.app = app
        self.cors_options = cors_options
        # 일반 엔드포인트용 CORS 미들웨어
        self.cors_middleware = CORSMiddleware(app, **cors_options)
    
    async def __call__(self, scope, receive, send):
        """ASGI 호출 처리"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        # SSE 엔드포인트인지 확인
        path = scope.get("path", "")
        is_sse = "/sse" in path or "/bridge/sse" in path or "/standard/sse" in path
        
        if is_sse:
            # SSE 엔드포인트는 직접 CORS 헤더 처리
            origin = None
            headers = dict(scope.get("headers", []))
            origin_bytes = headers.get(b"origin")
            if origin_bytes:
                origin = origin_bytes.decode("utf-8")
            
            async def sse_send(message):
                if message["type"] == "http.response.start":
                    # CORS 헤더 추가
                    headers = dict(message.get("headers", []))
                    cors_headers = [
                        (b"access-control-allow-origin", origin.encode() if origin else b"*"),
                        (b"access-control-allow-credentials", b"true"),
                        (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
                        (b"access-control-allow-headers", b"*"),
                        (b"cache-control", b"no-cache"),
                        (b"connection", b"keep-alive"),
                    ]
                    
                    # 기존 헤더에 추가 (중복 방지)
                    existing_headers = list(message.get("headers", []))
                    cors_header_names = {h[0] for h in cors_headers}
                    filtered_headers = [h for h in existing_headers if h[0] not in cors_header_names]
                    
                    message["headers"] = filtered_headers + cors_headers
                
                await send(message)
            
            # OPTIONS 요청 처리
            if scope["method"] == "OPTIONS":
                await send({
                    "type": "http.response.start",
                    "status": 204,
                    "headers": [
                        (b"access-control-allow-origin", origin.encode() if origin else b"*"),
                        (b"access-control-allow-credentials", b"true"),
                        (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
                        (b"access-control-allow-headers", b"*"),
                    ]
                })
                await send({"type": "http.response.body", "body": b""})
                return
            
            # SSE 엔드포인트 실행
            await self.app(scope, receive, sse_send)
        else:
            # 일반 엔드포인트는 표준 CORS 미들웨어 사용
            await self.cors_middleware(scope, receive, send)
