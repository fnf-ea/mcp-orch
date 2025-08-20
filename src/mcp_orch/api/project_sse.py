"""
프로젝트 중심 SSE 엔드포인트
프로젝트별 MCP 서버 접근 및 권한 제어
"""

from typing import Dict, Any, Optional
from uuid import UUID
import json
import logging

from fastapi import APIRouter, Request, HTTPException, status, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import and_

from ..database import get_db
from ..models import Project, ProjectMember, User, McpServer, ApiKey
from .jwt_auth import get_user_from_jwt_token
from ..core.controller import DualModeController

logger = logging.getLogger(__name__)

router = APIRouter(tags=["project-sse"])


# 사용자 인증 dependency 함수 (유연한 인증 정책)
async def get_current_user_for_project_sse_flexible(
    request: Request,
    project_id: UUID,
    db: Session = Depends(get_db)
) -> Optional[User]:
    """프로젝트 SSE API용 유연한 사용자 인증 함수"""
    
    # 프로젝트 보안 설정 조회
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )
    
    # SSE 연결인지 확인
    is_sse_request = request.url.path.endswith('/sse')
    
    # SSE 연결 시 인증 정책 확인
    if is_sse_request:
        if not project.sse_auth_required:
            logger.info(f"SSE connection allowed without auth for project {project_id}")
            return None  # 인증 없이 허용
    else:
        # 메시지 요청 시 인증 정책 확인
        if not project.message_auth_required:
            logger.info(f"Message request allowed without auth for project {project_id}")
            return None  # 인증 없이 허용
    
    # 인증이 필요한 경우 - JWT 토큰 또는 API 키 확인
    user = await get_user_from_jwt_token(request, db)
    if not user:
        # JWT 인증 실패 시 request.state.user 확인 (API 키 인증 결과)
        if hasattr(request.state, 'user') and request.state.user:
            user = request.state.user
            auth_type = "SSE" if is_sse_request else "Message"
            logger.info(f"Authenticated {auth_type} request via API key for project {project_id}, user={user.email}")
            return user
        
        auth_type = "SSE" if is_sse_request else "Message"
        logger.warning(f"{auth_type} authentication required but no valid token for project {project_id}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    
    logger.info(f"Authenticated {'SSE' if is_sse_request else 'Message'} request for project {project_id}, user={user.email}")
    return user


# 기존 인증 함수 (하위 호환성)
async def get_current_user_for_project_sse(
    request: Request,
    db: Session = Depends(get_db)
) -> User:
    """프로젝트 SSE API용 사용자 인증 함수 (기존 버전)"""
    user = await get_user_from_jwt_token(request, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    return user


async def _verify_project_access(
    project_id: UUID,
    current_user: User,
    db: Session
) -> Project:
    """프로젝트 접근 권한 확인"""
    
    # 프로젝트 멤버십 확인
    project_member = db.query(ProjectMember).filter(
        and_(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == current_user.id
        )
    ).first()
    
    if not project_member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found or access denied"
        )
    
    # 프로젝트 조회
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )
    
    return project


async def _verify_project_server_access(
    project_id: UUID,
    server_name: str,
    current_user: User,
    db: Session
) -> McpServer:
    """프로젝트 서버 접근 권한 확인"""
    
    # 프로젝트 접근 권한 확인
    await _verify_project_access(project_id, current_user, db)
    
    # 서버 존재 및 프로젝트 소속 확인
    server = db.query(McpServer).filter(
        and_(
            McpServer.project_id == project_id,
            McpServer.name == server_name
        )
    ).first()
    
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server '{server_name}' not found in this project"
        )
    
    return server


# SSE 엔드포인트는 standard_mcp.py에서만 구현
# 중복 제거를 위해 project_sse.py에서는 라우트 정의하지 않음


# 메시지 처리를 위한 헬퍼 함수
async def send_message_to_sse_session(session_id: str, message: Dict[str, Any]):
    """SSE 세션에 메시지 전송"""
    if hasattr(project_server_sse_endpoint, 'sessions'):
        session = project_server_sse_endpoint.sessions.get(session_id)
        if session:
            try:
                await session['queue'].put(message)
                logger.info(f"Message sent to SSE session {session_id}")
                return True
            except Exception as e:
                logger.error(f"Failed to send message to SSE session {session_id}: {e}")
    return False


# 프로젝트별 메시지 엔드포인트는 SSE 트랜스포트의 handle_post_message로 자동 처리됨
# 별도 구현 불필요 - SseServerTransport가 /messages/ 경로를 자동으로 처리


# 프로젝트별 서버 관리 API
@router.get("/projects/{project_id}/servers")
async def list_project_servers(
    project_id: UUID,
    current_user: User = Depends(get_current_user_for_project_sse),
    db: Session = Depends(get_db)
):
    """프로젝트의 MCP 서버 목록 조회 (stdio 및 SSE 모두 지원)"""
    
    # 프로젝트 접근 권한 확인
    await _verify_project_access(project_id, current_user, db)
    
    # 프로젝트 서버 목록 조회
    servers = db.query(McpServer).filter(
        McpServer.project_id == project_id
    ).all()
    
    result = []
    for server in servers:
        server_data = {
            "id": str(server.id),
            "name": server.name,
            "transport_type": server.transport_type,
            "timeout": server.timeout,
            "auto_approve": server.auto_approve,
            "disabled": not server.is_enabled,
            "status": server.status.value if server.status else "unknown",
            "created_at": server.created_at.isoformat() if server.created_at else None,
            "updated_at": server.updated_at.isoformat() if server.updated_at else None
        }
        
        if server.is_sse_server():
            # SSE 서버 정보
            server_data.update({
                "url": server.url,
                "headers_count": len(server.headers),
                "has_custom_headers": bool(server.headers)
            })
        else:
            # stdio 서버 정보
            server_data.update({
                "command": server.command,
                "args": server.args,
                "env_vars_count": len(server.env),
                "has_custom_env": bool(server.env)
            })
        
        result.append(server_data)
    
    return result


@router.post("/projects/{project_id}/servers")
async def add_project_server(
    project_id: UUID,
    server_data: Dict[str, Any],
    current_user: User = Depends(get_current_user_for_project_sse),
    db: Session = Depends(get_db)
):
    """프로젝트에 MCP 서버 추가 (stdio 및 SSE 모두 지원)"""
    
    # 프로젝트 접근 권한 확인 (Owner/Developer)
    project_member = db.query(ProjectMember).filter(
        and_(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == current_user.id,
            ProjectMember.role.in_(["owner", "developer"])
        )
    ).first()
    
    if not project_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only project owners and developers can add servers"
        )
    
    # 서버명 중복 확인
    existing_server = db.query(McpServer).filter(
        and_(
            McpServer.project_id == project_id,
            McpServer.name == server_data.get("name")
        )
    ).first()
    
    if existing_server:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Server name already exists in this project"
        )
    
    # 전송 타입 확인 및 검증
    transport_type = server_data.get("type", server_data.get("transport_type", "stdio"))
    
    # 서버 기본 설정
    server_config = {
        "project_id": project_id,
        "name": server_data.get("name"),
        "description": server_data.get("description"),
        "transport_type": transport_type,
        "timeout": server_data.get("timeout", 60),
        "auto_approve": server_data.get("auto_approve", server_data.get("autoApprove", [])),
        "is_enabled": not server_data.get("disabled", False),
        "created_by_id": current_user.id
    }
    
    if transport_type in ["sse", "http"]:
        # SSE 서버 설정
        url = server_data.get("url")
        if not url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="URL is required for SSE servers"
            )
        
        if not url.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="URL must start with http:// or https://"
            )
        
        server_config.update({
            "url": url
        })
        
        # SSE 서버는 command가 필요없음
        server_config["command"] = None
        
    else:
        # stdio 서버 설정
        command = server_data.get("command")
        if not command:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Command is required for stdio servers"
            )
        
        server_config.update({
            "command": command,
            "args": server_data.get("args", []),
            "env": server_data.get("env", {}),
            "cwd": server_data.get("cwd")
        })
    
    # 서버 생성
    server = McpServer(**server_config)
    
    # SSE 서버인 경우 헤더 설정
    if transport_type in ["sse", "http"] and "headers" in server_data:
        server.headers = server_data["headers"]
    
    db.add(server)
    db.commit()
    db.refresh(server)
    
    # 응답 데이터 구성 (전송 타입별)
    response_data = {
        "id": str(server.id),
        "name": server.name,
        "transport_type": server.transport_type,
        "timeout": server.timeout,
        "auto_approve": server.auto_approve,
        "disabled": not server.is_enabled,
        "status": server.status.value if server.status else "unknown",
        "created_at": server.created_at.isoformat() if server.created_at else None,
        "updated_at": server.updated_at.isoformat() if server.updated_at else None
    }
    
    if server.is_sse_server():
        response_data.update({
            "url": server.url,
            "headers_count": len(server.headers)
        })
    else:
        response_data.update({
            "command": server.command,
            "args": server.args,
            "env_count": len(server.env)
        })
    
    return response_data


@router.put("/projects/{project_id}/servers/{server_id}")
async def update_project_server(
    project_id: UUID,
    server_id: UUID,
    server_data: Dict[str, Any],
    current_user: User = Depends(get_current_user_for_project_sse),
    db: Session = Depends(get_db)
):
    """프로젝트 MCP 서버 정보 수정 (Owner/Developer만 가능)"""
    
    # 프로젝트 접근 권한 확인 (Owner/Developer)
    project_member = db.query(ProjectMember).filter(
        and_(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == current_user.id,
            ProjectMember.role.in_(["owner", "developer"])
        )
    ).first()
    
    if not project_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only project owners and developers can update servers"
        )
    
    # 서버 존재 및 프로젝트 소속 확인
    server = db.query(McpServer).filter(
        and_(
            McpServer.id == server_id,
            McpServer.project_id == project_id
        )
    ).first()
    
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found in this project"
        )
    
    # 서버명 변경 시 중복 확인
    if "name" in server_data and server_data["name"] != server.name:
        existing_server = db.query(McpServer).filter(
            and_(
                McpServer.project_id == project_id,
                McpServer.name == server_data["name"],
                McpServer.id != server_id
            )
        ).first()
        
        if existing_server:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Server name already exists in this project"
            )
    
    # 서버 정보 업데이트
    if "name" in server_data:
        server.name = server_data["name"]
    if "command" in server_data:
        server.command = server_data["command"]
    if "args" in server_data:
        server.args = server_data["args"]
    if "env" in server_data:
        server.env = server_data["env"]
    if "disabled" in server_data:
        server.is_enabled = not server_data["disabled"]
    
    from datetime import datetime
    server.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(server)
    
    return {
        "id": str(server.id),
        "name": server.name,
        "command": server.command,
        "args": server.args,
        "env": server.env,
        "disabled": server.disabled,
        "status": server.status.value if server.status else "unknown",
        "created_at": server.created_at.isoformat() if server.created_at else None,
        "updated_at": server.updated_at.isoformat() if server.updated_at else None
    }


@router.delete("/projects/{project_id}/servers/{server_id}")
async def delete_project_server(
    project_id: UUID,
    server_id: UUID,
    current_user: User = Depends(get_current_user_for_project_sse),
    db: Session = Depends(get_db)
):
    """프로젝트에서 MCP 서버 삭제 (Owner/Developer만 가능)"""
    
    # 프로젝트 접근 권한 확인 (Owner/Developer)
    project_member = db.query(ProjectMember).filter(
        and_(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == current_user.id,
            ProjectMember.role.in_(["owner", "developer"])
        )
    ).first()
    
    if not project_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only project owners and developers can delete servers"
        )
    
    # 서버 존재 및 프로젝트 소속 확인
    server = db.query(McpServer).filter(
        and_(
            McpServer.id == server_id,
            McpServer.project_id == project_id
        )
    ).first()
    
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found in this project"
        )
    
    # 서버 삭제
    server_name = server.name
    db.delete(server)
    db.commit()
    
    return {"message": f"Server '{server_name}' deleted successfully"}


@router.post("/projects/{project_id}/servers/{server_id}/toggle")
async def toggle_project_server(
    project_id: UUID,
    server_id: UUID,
    current_user: User = Depends(get_current_user_for_project_sse),
    db: Session = Depends(get_db)
):
    """프로젝트 MCP 서버 활성화/비활성화 토글 (Owner/Developer만 가능)"""
    
    # 프로젝트 접근 권한 확인 (Owner/Developer)
    project_member = db.query(ProjectMember).filter(
        and_(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == current_user.id,
            ProjectMember.role.in_(["owner", "developer"])
        )
    ).first()
    
    if not project_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only project owners and developers can toggle servers"
        )
    
    # 서버 존재 및 프로젝트 소속 확인
    server = db.query(McpServer).filter(
        and_(
            McpServer.id == server_id,
            McpServer.project_id == project_id
        )
    ).first()
    
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found in this project"
        )
    
    # 서버 상태 토글
    server.is_enabled = not server.is_enabled
    
    from datetime import datetime
    server.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(server)
    
    action = "disabled" if not server.is_enabled else "enabled"
    
    return {
        "message": f"Server '{server.name}' {action} successfully",
        "server": {
            "id": str(server.id),
            "name": server.name,
            "disabled": not server.is_enabled,
            "status": server.status.value if server.status else "unknown",
            "updated_at": server.updated_at.isoformat() if server.updated_at else None
        }
    }


# 프로젝트별 API 키 관리
@router.get("/projects/{project_id}/api-keys")
async def list_project_api_keys(
    project_id: UUID,
    current_user: User = Depends(get_current_user_for_project_sse),
    db: Session = Depends(get_db)
):
    """프로젝트 API 키 목록 조회 (Owner/Developer만 가능)"""
    
    # 프로젝트 접근 권한 확인 (Owner/Developer)
    project_member = db.query(ProjectMember).filter(
        and_(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == current_user.id,
            ProjectMember.role.in_(["owner", "developer"])
        )
    ).first()
    
    if not project_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only project owners and developers can view API keys"
        )
    
    # API 키 목록 조회
    api_keys = db.query(ApiKey).filter(
        ApiKey.project_id == project_id
    ).all()
    
    result = []
    for key in api_keys:
        result.append({
            "id": str(key.id),
            "name": key.name,
            "key_prefix": key.key_prefix,
            "is_active": key.is_active,
            "expires_at": key.expires_at.isoformat() if key.expires_at else None,
            "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
            "created_at": key.created_at.isoformat() if key.created_at else None
        })
    
    return result


@router.post("/projects/{project_id}/api-keys")
async def create_project_api_key(
    project_id: UUID,
    key_data: Dict[str, Any],
    current_user: User = Depends(get_current_user_for_project_sse),
    db: Session = Depends(get_db)
):
    """프로젝트 API 키 생성 (Owner만 가능)"""
    
    # 프로젝트 Owner 권한 확인
    project_member = db.query(ProjectMember).filter(
        and_(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == current_user.id,
            ProjectMember.role == "owner"
        )
    ).first()
    
    if not project_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only project owners can create API keys"
        )
    
    # API 키 생성 로직은 기존 ApiKey 모델의 generate_api_key 함수 활용
    from ..models.api_key import generate_api_key
    import hashlib
    
    api_key = generate_api_key()
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    key_prefix = api_key[:10]
    
    # API 키 저장
    new_key = ApiKey(
        project_id=project_id,
        name=key_data.get("name", "Default API Key"),
        key_hash=key_hash,
        key_prefix=key_prefix,
        created_by_id=current_user.id,
        permissions=key_data.get("permissions", {})
    )
    
    db.add(new_key)
    db.commit()
    db.refresh(new_key)
    
    return {
        "id": str(new_key.id),
        "name": new_key.name,
        "api_key": api_key,  # 생성 시에만 반환
        "key_prefix": new_key.key_prefix,
        "is_active": new_key.is_active,
        "expires_at": new_key.expires_at.isoformat() if new_key.expires_at else None,
        "created_at": new_key.created_at.isoformat() if new_key.created_at else None
    }


@router.delete("/projects/{project_id}/api-keys/{key_id}")
async def delete_project_api_key(
    project_id: UUID,
    key_id: UUID,
    current_user: User = Depends(get_current_user_for_project_sse),
    db: Session = Depends(get_db)
):
    """프로젝트 API 키 삭제 (Owner/Developer만 가능)"""
    
    # 프로젝트 권한 확인 (Owner 또는 Developer)
    project_member = db.query(ProjectMember).filter(
        and_(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == current_user.id,
            ProjectMember.role.in_(["owner", "developer"])
        )
    ).first()
    
    if not project_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only project owners and developers can delete API keys"
        )
    
    # API 키 조회
    api_key = db.query(ApiKey).filter(
        and_(
            ApiKey.id == key_id,
            ApiKey.project_id == project_id
        )
    ).first()
    
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found"
        )
    
    # API 키 삭제
    key_name = api_key.name
    db.delete(api_key)
    db.commit()
    
    return {"message": f"API key '{key_name}' deleted successfully"}


# 프로젝트별 Cline 설정 생성
@router.get("/projects/{project_id}/cline-config")
async def get_project_cline_config(
    project_id: UUID,
    unified: Optional[bool] = None,
    current_user: User = Depends(get_current_user_for_project_sse),
    db: Session = Depends(get_db)
):
    """프로젝트별 MCP 설정 파일 자동 생성 (Claude, Cursor 등 호환)
    
    Args:
        unified: True일 경우 통합 MCP 서버 엔드포인트 사용, False일 경우 개별 서버 설정
                None일 경우 프로젝트 설정값(unified_mcp_enabled) 사용
    """
    
    # 프로젝트 접근 권한 확인
    project = await _verify_project_access(project_id, current_user, db)
    
    # unified 모드 결정: 파라미터가 제공되지 않으면 프로젝트 설정 사용
    use_unified = unified if unified is not None else project.unified_mcp_enabled
    
    # 프로젝트 서버 목록 조회
    servers = db.query(McpServer).filter(
        and_(
            McpServer.project_id == project_id,
            McpServer.is_enabled == True
        )
    ).all()
    
    # 프로젝트 API 키 조회 (첫 번째 활성 키 사용)
    api_key = db.query(ApiKey).filter(
        and_(
            ApiKey.project_id == project_id,
            ApiKey.is_active == True
        )
    ).first()
    
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active API key found for this project. Please create an API key first."
        )
    
    # 동적 base URL 확인 (환경변수 또는 요청에서 추출)
    from ..config import get_mcp_server_base_url
    from starlette.requests import Request
    base_url = get_mcp_server_base_url()
    
    # Cline 설정 생성
    mcp_servers = {}
    
    if use_unified:
        # 통합 MCP 서버 모드 - 하나의 SSE 엔드포인트로 모든 서버 접근
        server_key = f"mcp-orch-unified-{project_id}"
        
        # 프로젝트의 JWT 인증 설정 확인 (모든 서버가 JWT 필요한지 확인)
        requires_jwt = any(server.get_effective_jwt_auth_required() for server in servers)
        
        server_config = {
            "type": "sse",
            "url": f"{base_url}/projects/{project_id}/unified/sse",
            "timeout": 60,
            "disabled": False
        }
        
        # JWT 인증이 필요한 경우 헤더에 API 키 설정
        if requires_jwt:
            server_config["headers"] = {
                "Authorization": f"Bearer ${{{api_key.key_prefix}...}}"
            }
        
        mcp_servers[server_key] = server_config
        
        # 개별 SSE 서버들도 통합 모드에 포함되지만 별도 엔드포인트로 접근 가능
        sse_servers = [s for s in servers if s.is_sse_server()]
        if sse_servers:
            for server in sse_servers:
                individual_key = f"mcp-orch-{project_id}-{server.name}"
                individual_config = {
                    "type": "sse",
                    "url": f"{base_url}/projects/{project_id}/servers/{server.name}/sse",
                    "timeout": server.timeout,
                    "disabled": False
                }
                
                # 서버별 JWT 인증 확인
                if server.get_effective_jwt_auth_required():
                    individual_config["headers"] = {
                        "Authorization": f"Bearer ${{{api_key.key_prefix}...}}"
                    }
                
                # 서버의 커스텀 헤더 추가
                if server.headers:
                    if "headers" not in individual_config:
                        individual_config["headers"] = {}
                    individual_config["headers"].update(server.headers)
                
                mcp_servers[f"{individual_key}-direct"] = individual_config
        
        sse_count = len([s for s in servers if s.is_sse_server()])
        stdio_count = len([s for s in servers if s.is_stdio_server()])
        
        instructions = [
            "🚀 UNIFIED MCP SERVER CONFIGURATION",
            "1. Save this configuration as 'mcp_settings.json' in your project root",
            "2. Configure Claude Desktop, Cursor, or other MCP clients to use this settings file",
            "3. Replace placeholder API keys with your actual full API key where needed",
            "4. This unified endpoint provides access to ALL project servers through a single connection",
            f"5. Tools are namespaced with format: 'server_name.tool_name' (separator: '.')",
            f"6. Access {len(servers)} servers ({stdio_count} stdio + {sse_count} SSE) through one endpoint",
            "7. Error isolation: individual server failures won't affect other servers",
            "8. Health monitoring and recovery tools available through 'orchestrator.*' meta tools",
            "9. Individual SSE servers also available as direct endpoints if needed"
        ]
        
    else:
        # 개별 서버 모드 (기존 방식 + SSE 지원)
        for server in servers:
            server_key = f"project-{project_id}-{server.name}"
            
            # 서버별 JWT 인증 설정 확인
            jwt_auth_required = server.get_effective_jwt_auth_required()
            
            if server.is_sse_server():
                # SSE 서버 설정
                server_config = {
                    "type": "sse",
                    "url": server.url,  # 데이터베이스에 저장된 직접 URL
                    "timeout": server.timeout,
                    "disabled": False
                }
                
                # JWT 인증이 필요한 경우 헤더에 API 키 설정
                if jwt_auth_required:
                    server_config["headers"] = {
                        "Authorization": f"Bearer ${{{api_key.key_prefix}...}}"
                    }
                
                # 서버의 커스텀 헤더 추가
                if server.headers:
                    if "headers" not in server_config:
                        server_config["headers"] = {}
                    server_config["headers"].update(server.headers)
                
            else:
                # stdio 서버 설정 (기존 방식)
                server_config = {
                    "type": "stdio",
                    "command": server.command,
                    "args": server.args if server.args else [],
                    "env": server.env if server.env else {},
                    "timeout": server.timeout,
                    "disabled": False
                }
                
                # JWT 인증이 필요한 경우만 환경변수에 API 키 설정 추가
                if jwt_auth_required:
                    if not server_config["env"]:
                        server_config["env"] = {}
                    server_config["env"]["MCP_API_KEY"] = f"${{{api_key.key_prefix}...}}"
            
            mcp_servers[server_key] = server_config
        
        sse_count = len([s for s in servers if s.is_sse_server()])
        stdio_count = len([s for s in servers if s.is_stdio_server()])
        
        instructions = [
            "📋 INDIVIDUAL SERVERS CONFIGURATION",
            "1. Save this configuration as 'mcp_settings.json' in your project root",
            "2. Configure Claude Desktop, Cursor, or other MCP clients to use this settings file", 
            "3. Replace placeholder API keys with your actual full API key where needed",
            "4. Servers without authentication headers/env vars do not require auth",
            f"5. Mixed transport types: {stdio_count} stdio + {sse_count} SSE servers",
            "6. stdio servers run as separate processes, SSE servers connect to remote URLs",
            "7. SSE servers support custom HTTP headers for authentication and configuration"
        ]
    
    cline_config = {
        "mcpServers": mcp_servers
    }
    
    return {
        "project_id": str(project_id),
        "project_name": project.name,
        "config": cline_config,
        "servers_count": len(servers),
        "api_key_prefix": api_key.key_prefix,
        "mode": "unified" if use_unified else "individual",
        "unified_endpoint": f"{base_url}/projects/{project_id}/unified/sse" if use_unified else None,
        "instructions": instructions
    }
