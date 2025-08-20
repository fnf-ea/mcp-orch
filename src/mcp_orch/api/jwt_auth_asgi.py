"""
JWT 인증 ASGI 미들웨어

SSE 호환을 위한 순수 ASGI 미들웨어 구현
"""

import os
import logging
import hashlib
import json
import base64
from typing import Optional
from datetime import datetime

import jwt
from jwt import PyJWTError as JWTError
from sqlalchemy.orm import Session

from ..models import User, ApiKey, Project
from ..database import get_db

logger = logging.getLogger(__name__)

# JWT 비밀 키 (환경 변수에서 가져오기)
AUTH_SECRET = os.getenv("AUTH_SECRET", "your-secret-key-here")


class JWTAuthASGIMiddleware:
    """
    통합 인증 ASGI 미들웨어
    JWT 토큰과 API 키 인증을 모두 지원합니다.
    
    SSE 스트리밍과의 충돌을 방지하기 위해 순수 ASGI 미들웨어로 구현됩니다.
    """
    
    def __init__(self, app, settings=None):
        self.app = app
        self.settings = settings
        
        # API 키 설정 (설정이 있는 경우)
        self.api_keys = {}
        if settings and hasattr(settings, 'security') and settings.security.api_keys:
            self.api_keys = {
                key_info["key"]: key_info
                for key_info in settings.security.api_keys
            }
            logger.info(f"Loaded {len(self.api_keys)} API keys")
        
        # 공개 경로 (인증 불필요)
        self.public_paths = [
            "/api/auth",
            "/api/teams",
            "/api/users/signup",
            "/api/users/login",
            "/api/users/test",
            "/api/users/test-db",
            "/api/status",
            "/api/health",
            "/health",
            "/sse",
            "/docs",
            "/openapi.json",
            "/redoc",
            "/",
            "/favicon.ico"
        ]
    
    async def __call__(self, scope, receive, send):
        """ASGI 호출 처리"""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        # 인증 처리
        path = scope.get("path", "")
        
        # SSE 엔드포인트는 특별 처리
        is_sse = "/sse" in path or path.endswith("/messages")
        
        # 인증 비활성화 옵션 확인
        disable_auth = os.getenv("DISABLE_AUTH", "false").lower() == "true"
        if disable_auth:
            logger.debug("Authentication is DISABLED (DISABLE_AUTH=true)")
            scope["user"] = None
            await self.app(scope, receive, send)
            return
        
        # 헤더에서 Authorization 추출
        headers = dict(scope.get("headers", []))
        auth_header_bytes = headers.get(b"authorization")
        
        if auth_header_bytes:
            auth_header = auth_header_bytes.decode("utf-8")
            
            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]
                
                # 토큰 타입 확인
                if token.startswith("project_"):
                    # 프로젝트 API 키
                    user = await self._process_project_api_key(token)
                    scope["user"] = user
                    
                elif token.startswith("mch_"):
                    # MCP API 키
                    user = await self._process_mcp_api_key(token)
                    scope["user"] = user
                    
                else:
                    # JWT 토큰
                    user = await self._process_jwt_token(token)
                    scope["user"] = user
            else:
                scope["user"] = None
        else:
            scope["user"] = None
        
        # Request state에 사용자 정보 설정을 위한 래퍼
        async def wrapped_receive():
            message = await receive()
            return message
        
        async def wrapped_send(message):
            await send(message)
        
        # scope를 수정하여 request.state.user 지원
        if "extensions" not in scope:
            scope["extensions"] = {}
        scope["extensions"]["user"] = scope.get("user")
        
        # 앱 실행
        await self.app(scope, wrapped_receive, wrapped_send)
    
    async def _process_project_api_key(self, api_key: str) -> Optional[User]:
        """프로젝트 API 키 처리"""
        db = next(get_db())
        try:
            # API 키 해시 생성
            key_hash = hashlib.sha256(api_key.encode()).hexdigest()
            
            # 데이터베이스에서 API 키 조회
            api_key_record = db.query(ApiKey).filter(
                ApiKey.key_hash == key_hash,
                ApiKey.is_active == True
            ).first()
            
            # 호환성을 위한 평문 검색
            if not api_key_record:
                api_key_record = db.query(ApiKey).filter(
                    ApiKey.key_hash == api_key,
                    ApiKey.is_active == True
                ).first()
            
            if not api_key_record:
                return None
            
            # 사용 시간 업데이트
            api_key_record.last_used_at = datetime.utcnow()
            db.commit()
            
            # 프로젝트 조회
            project = db.query(Project).filter(
                Project.id == api_key_record.project_id
            ).first()
            
            if not project:
                return None
            
            # API 키 생성자 반환
            user = db.query(User).filter(
                User.id == api_key_record.created_by_id
            ).first()
            
            return user
            
        except Exception as e:
            logger.error(f"Error processing project API key: {e}")
            return None
        finally:
            db.close()
    
    async def _process_mcp_api_key(self, api_key: str) -> Optional[User]:
        """MCP API 키 처리"""
        # 프로젝트 API 키와 동일한 로직 사용
        return await self._process_project_api_key(api_key)
    
    async def _process_jwt_token(self, token: str) -> Optional[User]:
        """JWT 토큰 처리"""
        try:
            # 토큰 헤더에서 알고리즘 확인
            header_b64 = token.split('.')[0]
            header_b64 += '=' * (4 - len(header_b64) % 4)
            header = json.loads(base64.b64decode(header_b64))
            
            algorithm = header.get('alg', 'HS256')
            
            if algorithm == 'none':
                # NextAuth.js 개발 환경 토큰
                payload = jwt.decode(
                    token,
                    key="",
                    algorithms=["none"],
                    options={
                        "verify_signature": False,
                        "verify_exp": True,
                        "verify_aud": False,
                        "verify_iss": False
                    }
                )
            else:
                # 일반 JWT 토큰
                payload = jwt.decode(
                    token,
                    key=AUTH_SECRET,
                    algorithms=[algorithm],
                    options={
                        "verify_signature": True,
                        "verify_exp": True,
                        "verify_aud": False,
                        "verify_iss": False
                    }
                )
            
            user_id = payload.get("sub")
            if not user_id:
                return None
            
            # 데이터베이스에서 사용자 조회
            db = next(get_db())
            try:
                user = db.query(User).filter(User.id == user_id).first()
                return user
            finally:
                db.close()
                
        except jwt.ExpiredSignatureError:
            logger.debug("JWT token expired")
            return None
        except JWTError as e:
            logger.debug(f"Invalid JWT token: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error processing JWT: {e}")
            return None


# FastAPI 호환성을 위한 헬퍼 미들웨어
class RequestStateMiddleware:
    """
    ASGI scope의 user를 request.state.user로 매핑하는 미들웨어
    """
    
    def __init__(self, app):
        self.app = app
    
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        # Request 객체가 생성될 때 state.user 설정
        from starlette.requests import Request
        
        original_receive = receive
        user = scope.get("extensions", {}).get("user") or scope.get("user")
        
        async def receive_wrapper():
            message = await original_receive()
            # Request 객체에 user 정보 주입
            if hasattr(receive_wrapper, '_request'):
                receive_wrapper._request.state.user = user
            return message
        
        # Request 생성 시 user 정보 설정을 위한 훅
        def set_request(request):
            request.state.user = user
            receive_wrapper._request = request
        
        scope["set_request_hook"] = set_request
        
        await self.app(scope, receive_wrapper, send)