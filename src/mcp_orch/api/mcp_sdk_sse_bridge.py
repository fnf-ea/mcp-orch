"""
MCP SDK SSE Bridge - 하이브리드 구현
mcp-orch URL 구조 + python-sdk 표준 내부 구현

이 모듈은 mcp-orch의 프로젝트별 URL 구조를 유지하면서
python-sdk의 표준 SseServerTransport를 내부적으로 활용합니다.
"""

import asyncio
import logging
from typing import Dict, Optional, Any
from uuid import UUID
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import APIRouter, Request, HTTPException, status, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import and_

# python-sdk 표준 구현 임포트
from mcp.server.sse import SseServerTransport
from mcp.server.lowlevel import Server
from mcp.shared.message import SessionMessage
import mcp.types as types

from ..database import get_db
from ..models import Project, McpServer, User, ClientSession, LogLevel, LogCategory
from .jwt_auth import get_user_from_jwt_token
from ..services.mcp_connection_service import mcp_connection_service
from ..services.server_log_service import get_log_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp-sdk-sse-bridge"])

# Starlette 라우트 등록을 위한 임포트
from starlette.routing import Route, Mount
from starlette.applications import Starlette
from starlette.responses import Response as StarletteResponse


class ProjectMCPTransportManager:
    """프로젝트별 MCP Transport 관리자"""
    
    def __init__(self):
        self.transports: Dict[str, SseServerTransport] = {}
        self.mcp_servers: Dict[str, Server] = {}
    
    def get_transport_key(self, project_id: str, server_name: str) -> str:
        """프로젝트와 서버명으로 고유 키 생성"""
        return f"{project_id}:{server_name}"
    
    def get_transport(self, project_id: str, server_name: str) -> SseServerTransport:
        """프로젝트별 SSE Transport 인스턴스 반환"""
        key = self.get_transport_key(project_id, server_name)
        
        if key not in self.transports:
            # mcp-orch URL 구조 유지: 프로젝트별 메시지 엔드포인트
            # 중요: 메시지 엔드포인트는 POST 메시지를 받을 경로
            endpoint = f"/projects/{project_id}/servers/{server_name}/bridge/messages"
            self.transports[key] = SseServerTransport(endpoint)
            logger.info(f"✅ Created new SSE transport for {key} with message endpoint: {endpoint}")
            logger.info(f"   SSE endpoint: /projects/{project_id}/servers/{server_name}/bridge/sse")
        
        return self.transports[key]
    
    def get_mcp_server(self, project_id: str, server_name: str, server_config: Dict[str, Any]) -> Server:
        """프로젝트별 MCP Server 인스턴스 반환"""
        key = self.get_transport_key(project_id, server_name)
        
        if key not in self.mcp_servers:
            # MCP 서버 생성 (이름은 프로젝트:서버명 형식)
            server = Server(f"mcp-orch-{server_name}")
            
            # 실제 MCP 서버의 도구들을 동적으로 등록
            # TODO: 기존 mcp_connection_service와 통합하여 실제 도구 로드
            
            self.mcp_servers[key] = server
            logger.info(f"Created new MCP server instance for {key}")
        
        return self.mcp_servers[key]
    
    def cleanup_transport(self, project_id: str, server_name: str):
        """Transport 정리"""
        key = self.get_transport_key(project_id, server_name)
        
        if key in self.transports:
            del self.transports[key]
            logger.info(f"Cleaned up transport for {key}")
        
        if key in self.mcp_servers:
            del self.mcp_servers[key]
            logger.info(f"Cleaned up MCP server for {key}")


# 전역 Transport Manager 인스턴스
transport_manager = ProjectMCPTransportManager()


async def get_current_user_for_mcp_sse_bridge(
    request: Request,
    project_id: UUID,
    server_name: str,
    db: Session = Depends(get_db)
) -> Optional[User]:
    """MCP SSE Bridge용 사용자 인증 (DISABLE_AUTH 지원, 서버별 인증 설정)"""
    
    import os
    
    # DISABLE_AUTH 환경 변수 확인
    disable_auth = os.getenv("DISABLE_AUTH", "").lower() == "true"
    
    if disable_auth:
        logger.info(f"⚠️ Authentication disabled for SSE bridge request to project {project_id}, server {server_name}")
        # 인증이 비활성화된 경우 None 반환 (인증 없이 진행)
        return None
    
    # 서버 및 프로젝트 정보 조회
    server = db.query(McpServer).filter(
        McpServer.project_id == project_id,
        McpServer.name == server_name,
        McpServer.is_enabled == True
    ).first()
    
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server '{server_name}' not found or disabled in project {project_id}"
        )
    
    # SSE 연결인지 확인
    is_sse_request = request.url.path.endswith('/sse')
    
    # 서버별 JWT 인증 정책 확인 (서버 설정 > 프로젝트 기본값)
    auth_required = server.get_effective_jwt_auth_required()
    
    if not auth_required:
        auth_type = "SSE" if is_sse_request else "Message"
        logger.info(f"{auth_type} request allowed without auth for project {project_id}, server {server_name}")
        return None  # 인증 없이 허용
    
    # JWT 인증 시도
    user = await get_user_from_jwt_token(request, db)
    if not user:
        # API 키 인증 확인
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
    
    logger.info(f"Authenticated SSE bridge request for project {project_id}, user={user.email}")
    return user


@router.get("/projects/{project_id}/servers/{server_name}/sse")
@router.get("/projects/{project_id}/servers/{server_name}/bridge/sse")
async def mcp_sse_bridge_endpoint(
    project_id: UUID,
    server_name: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    MCP SSE Bridge 엔드포인트
    
    mcp-orch의 프로젝트별 URL 구조를 유지하면서
    python-sdk 표준 SSE Transport를 내부적으로 사용
    """
    
    try:
        # 사용자 인증 (서버별 인증 설정 포함)
        current_user = await get_current_user_for_mcp_sse_bridge(request, project_id, server_name, db)
        
        if current_user:
            logger.info(f"MCP SSE Bridge connection: project_id={project_id}, server={server_name}, user={current_user.email}")
        else:
            logger.info(f"MCP SSE Bridge connection (no auth): project_id={project_id}, server={server_name}")
        
        # 서버 존재 확인
        server_record = db.query(McpServer).filter(
            and_(
                McpServer.project_id == project_id,
                McpServer.name == server_name,
                McpServer.is_enabled == True
            )
        ).first()
        
        if not server_record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Server '{server_name}' not found or disabled in project {project_id}"
            )
        
        # Transport 가져오기
        transport = transport_manager.get_transport(str(project_id), server_name)
        
        logger.info(f"🚀 Starting MCP SSE Bridge for server {server_name}")
        logger.info(f"   Project ID: {project_id}")
        logger.info(f"   Server: {server_name}")
        logger.info(f"   Request path: {request.url.path}")
        logger.info(f"   Headers: {dict(request.headers)}")
        
        # python-sdk 표준 SSE 연결 사용
        logger.info(f"📡 Establishing SSE connection with python-sdk SseServerTransport")
        
        try:
            async with transport.connect_sse(
                request.scope, 
                request.receive, 
                request._send
            ) as streams:
                read_stream, write_stream = streams
                logger.info(f"✅ SSE streams established for {server_name}")
                
                # 초기 연결 안정화를 위한 짧은 지연
                # 클라이언트가 SSE 스트림을 완전히 설정할 시간 제공
                await asyncio.sleep(0.1)
                logger.info(f"📡 SSE stream stabilized, starting MCP session for {server_name}")
                
                # MCP 서버 세션 실행
                await run_mcp_bridge_session(
                    read_stream, 
                    write_stream, 
                    project_id, 
                    server_name, 
                    server_record,
                    request
                )
        except Exception as e:
            logger.error(f"❌ SSE connection failed: {e}", exc_info=True)
            raise
        
        # 빈 응답 반환 (python-sdk 예제에 따라)
        return Response()
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"MCP SSE Bridge error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"MCP SSE Bridge connection failed: {str(e)}"
        )


# POST 메시지 엔드포인트를 위한 ASGI 앱 생성
def create_post_message_handler(project_id: str, server_name: str):
    """프로젝트별 POST 메시지 핸들러 생성"""
    
    transport = transport_manager.get_transport(project_id, server_name)
    
    async def handle_post_message(scope, receive, send):
        """POST 메시지 처리 (python-sdk SseServerTransport 사용)"""
        try:
            await transport.handle_post_message(scope, receive, send)
        except Exception as e:
            logger.error(f"Error in POST message handler for {project_id}:{server_name}: {e}")
            # 에러 응답
            response = StarletteResponse("Internal Server Error", status_code=500)
            await response(scope, receive, send)
    
    return handle_post_message


@router.post("/projects/{project_id}/servers/{server_name}/messages")
@router.post("/projects/{project_id}/servers/{server_name}/bridge/messages")
async def mcp_bridge_post_messages(
    project_id: UUID,
    server_name: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    MCP Bridge POST 메시지 엔드포인트
    
    python-sdk SseServerTransport의 handle_post_message를 활용
    """
    
    try:
        # 사용자 인증 (서버별 인증 설정 포함)
        current_user = await get_current_user_for_mcp_sse_bridge(request, project_id, server_name, db)
        
        if current_user:
            logger.info(f"MCP Bridge POST message: project_id={project_id}, server={server_name}, user={current_user.email}")
        else:
            logger.info(f"MCP Bridge POST message (no auth): project_id={project_id}, server={server_name}")
        
        # 서버 존재 확인
        server_record = db.query(McpServer).filter(
            and_(
                McpServer.project_id == project_id,
                McpServer.name == server_name,
                McpServer.is_enabled == True
            )
        ).first()
        
        if not server_record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Server '{server_name}' not found or disabled in project {project_id}"
            )
        
        # Transport 가져오기
        transport = transport_manager.get_transport(str(project_id), server_name)
        
        # python-sdk 표준 POST 메시지 처리 사용
        await transport.handle_post_message(
            request.scope,
            request.receive,
            request._send
        )
        
        # 응답은 transport가 직접 처리
        return Response(status_code=202)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"MCP Bridge POST message error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"MCP Bridge POST message failed: {str(e)}"
        )


async def run_mcp_bridge_session(
    read_stream,
    write_stream, 
    project_id: UUID,
    server_name: str,
    server_record: McpServer,
    request: Request = None
):
    """
    MCP Bridge 세션 실행
    
    실제 MCP 서버의 도구를 로드하고 python-sdk Server 클래스로 프록시
    """
    
    logger.info(f"Starting MCP bridge session for {server_name}")
    
    # 클라이언트 세션 생성 및 관리
    from ..database import get_db_session
    from uuid import uuid4
    
    session_id = str(uuid4())
    user_agent = request.headers.get("user-agent") if request else None
    client_ip = None
    
    # IP 주소 추출
    if request:
        # X-Forwarded-For 헤더 확인 (프록시/로드밸런서 사용 시)
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()
        else:
            # 직접 연결인 경우 클라이언트 IP
            if hasattr(request, 'client') and request.client:
                client_ip = request.client.host
    
    # 클라이언트 타입 추정
    client_type = "unknown"
    if user_agent:
        if "cline" in user_agent.lower():
            client_type = "cline"
        elif "cursor" in user_agent.lower():
            client_type = "cursor"
        elif "vscode" in user_agent.lower():
            client_type = "vscode"
        elif "roo" in user_agent.lower():
            client_type = "roo"
        elif user_agent.lower() == "node":
            client_type = "node_client"  # Node.js 기반 클라이언트
    
    logger.info(f"🔗 Creating session {session_id} for {client_type} client (IP: {client_ip}, User-Agent: '{user_agent}')")
    
    # 데이터베이스 세션 생성
    db = get_db_session()
    client_session = None
    
    try:
        # ClientSession 생성 (실제 데이터베이스 스키마에 맞게 수정)
        from datetime import datetime, timedelta
        import uuid
        
        client_session = ClientSession(
            id=uuid.UUID(session_id),
            session_token=f"session_{session_id}",
            user_id=None,  # 인증 사용자가 있으면 설정
            project_id=project_id,
            server_id=str(server_record.id),
            client_name=client_type,  # client_type -> client_name
            user_agent=user_agent,
            ip_address=client_ip,
            status='active',
            expires_at=datetime.utcnow() + timedelta(hours=24)  # 24시간 후 만료
        )
        
        db.add(client_session)
        db.commit()
        
        logger.info(f"✅ ClientSession created: {session_id}")
        
        # ServerLog에 연결 이벤트 기록 (별도 DB 세션 사용)
        try:
            from ..services.server_log_service import ServerLogService
            with next(get_db()) as log_db:
                log_service = ServerLogService(log_db)
                log_service.add_log(
                    server_id=server_record.id,
                    project_id=project_id,
                    level=LogLevel.INFO,
                    category=LogCategory.CONNECTION,
                    message=f"Client session started: {client_type} client connected",
                    details={
                        "session_id": session_id,
                        "client_type": client_type,
                        "client_ip": client_ip,
                        "user_agent": user_agent
                    }
                )
            logger.info(f"📝 Connection log recorded for session {session_id}")
        except Exception as log_error:
            logger.error(f"Failed to record connection log: {log_error}")
        
        # 서버 설정 구성
        server_config = _build_server_config_from_db(server_record)
        if not server_config:
            raise ValueError("Failed to build server configuration")
        
        logger.info(f"🔧 Building MCP server for {server_name}")
        logger.info(f"   Config: transport={server_config.get('transport_type', 'stdio')}, command={server_config.get('command', 'N/A')}")
        
        # MCP Server 인스흉 생성
        mcp_server = Server(f"mcp-orch-{server_name}")
        
        # 실제 MCP 서버에서 도구 목록 동적 로드
        @mcp_server.list_tools()
        async def list_tools():
            try:
                logger.info(f"🔍 Loading tools for SSE bridge server: {server_name}")
                logger.info(f"   Session ID: {session_id}")
                logger.info(f"   Client: {client_type}")
                
                # SSE 브리지 서버는 자체 도구를 정의하거나 프록시 역할을 수행
                # 기본 SSE 브리지 도구 추가 (테스트용)
                tool_list = []
                
                # 기본 테스트 도구 추가
                test_tool = types.Tool(
                    name="sse_bridge_test",
                    description="Test tool for SSE bridge connectivity",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "message": {
                                "type": "string",
                                "description": "Test message to echo"
                            }
                        },
                        "required": ["message"]
                    }
                )
                tool_list.append(test_tool)
                logger.info(f"  - Added test tool: sse_bridge_test")
                
                # 브리지 서버가 stdio 서버의 프록시 역할을 하는 경우
                # command가 설정되어 있을 때만 실제 stdio 서버 도구를 가져옴
                if server_config.get('command'):
                    logger.info(f"🔍 Bridge has stdio backend - loading tools from stdio server")
                    logger.info(f"   Command: {server_config.get('command')}")
                    logger.info(f"   Args: {server_config.get('args', [])}")
                    
                    try:
                        # mcp_connection_service를 사용하여 도구 가져오기
                        # 실제 서버 ID는 server_record.id를 사용
                        from ..services.mcp_connection_service import mcp_connection_service
                        
                        # stdio 서버 연결 및 도구 목록 가져오기
                        filtered_tools = await mcp_connection_service.get_server_tools(
                            str(server_record.id),  # 실제 서버 ID 사용
                            server_config
                        )
                        
                        if filtered_tools:
                            logger.info(f"📋 Loaded {len(filtered_tools)} tools from stdio backend")
                            
                            # python-sdk 형식으로 변환
                            for tool in filtered_tools:
                                # 도구 정보 추출
                                tool_name = tool.get("name", "")
                                tool_desc = tool.get("description", "")
                                tool_schema = tool.get("inputSchema") or tool.get("schema") or {
                                    "type": "object",
                                    "properties": {},
                                    "required": []
                                }
                                
                                # 로그 출력
                                logger.info(f"  - Converting tool: {tool_name}")
                                logger.debug(f"    Schema: {tool_schema}")
                                
                                tool_obj = types.Tool(
                                    name=tool_name,
                                    description=tool_desc,
                                    inputSchema=tool_schema
                                )
                                tool_list.append(tool_obj)
                        else:
                            logger.warning(f"⚠️ No tools returned from stdio backend")
                    except Exception as stdio_error:
                        logger.error(f"❌ Failed to load stdio backend tools: {stdio_error}", exc_info=True)
                        # stdio 도구 로드 실패 시 계속 진행 (기본 도구만 사용)
                
                # SSE 브리지 전용 도구 추가 (예: search, web_fetch 등)
                # Brave Search 서버인 경우 검색 도구 추가
                if server_name == "brave-search":
                    brave_search_tool = types.Tool(
                        name="brave_web_search",
                        description="Search the web using Brave Search API",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "The search query"
                                },
                                "count": {
                                    "type": "integer",
                                    "description": "Number of results to return (default: 10)",
                                    "default": 10
                                }
                            },
                            "required": ["query"]
                        }
                    )
                    tool_list.append(brave_search_tool)
                    logger.info(f"  - Added Brave Search tool: brave_web_search")
                
                if not tool_list:
                    logger.warning(f"⚠️ No tools available for SSE bridge server {server_name}")
                else:
                    logger.info(f"✅ Successfully loaded {len(tool_list)} tools for {server_name}")
                    for tool in tool_list:
                        logger.info(f"     - {tool.name}: {tool.description[:50]}..." if len(tool.description) > 50 else f"     - {tool.name}: {tool.description}")
                
                # ServerLog에 도구 로딩 완료 이벤트 기록 (별도 DB 세션 사용)
                try:
                    from ..services.server_log_service import ServerLogService
                    with next(get_db()) as log_db:
                        log_service = ServerLogService(log_db)
                        log_service.add_log(
                            server_id=server_record.id,
                            project_id=project_id,
                            level=LogLevel.INFO,
                            category=LogCategory.SYSTEM,
                            message=f"SSE Bridge tools loaded: {len(tool_list)} tools available",
                            details={
                                "session_id": session_id,
                                "tool_count": len(tool_list),
                                "tool_names": [tool.name for tool in tool_list] if tool_list else [],
                                "bridge_mode": "SSE",
                                "has_stdio_backend": bool(server_config.get('command'))
                            }
                        )
                    logger.info(f"📝 Tool loading log recorded for session {session_id}")
                except Exception as log_error:
                    logger.error(f"Failed to record tool loading log: {log_error}")
                
                return tool_list
                
            except Exception as e:
                logger.error(f"Error loading tools for SSE bridge {server_name}: {e}")
                # 에러 시 빈 도구 목록 반환
                return []
        
        # 도구 실행을 실제 서버로 프록시
        @mcp_server.call_tool()
        async def call_tool(name: str, arguments: dict):
            tool_log_db = None  # 세션 변수 초기화
            try:
                logger.info(f"🔨 Processing tool call for {server_name}: {name}")
                logger.info(f"   Arguments: {arguments}")
                logger.info(f"   Session: {session_id}")
                
                # 도구 호출 로그용 데이터베이스 세션 생성
                tool_log_db = get_db_session()
                
                try:
                    # 세션 활동 업데이트
                    if client_session:
                        client_session.last_accessed_at = datetime.utcnow()
                        client_session.total_requests += 1
                        db.commit()
                    
                    # SSE 브리지 테스트 도구 처리
                    if name == "sse_bridge_test":
                        message = arguments.get("message", "")
                        logger.info(f"🧪 Executing SSE bridge test tool with message: {message}")
                        
                        result = {
                            "status": "success",
                            "echo": message,
                            "server": server_name,
                            "session": session_id,
                            "timestamp": datetime.utcnow().isoformat(),
                            "message": f"SSE Bridge test successful: {message}"
                        }
                    
                    # Brave Search 도구 실행
                    if server_name == "brave-search" and name == "brave_web_search":
                        # Brave Search API 호출 시뮬레이션
                        # 실제 구현시 API 키와 실제 API 호출 필요
                        query = arguments.get("query", "")
                        count = arguments.get("count", 10)
                        
                        logger.info(f"Executing Brave Search for query: {query} (count: {count})")
                        
                        # 시뮬레이션 결과 (실제로는 Brave API 호출)
                        result = {
                            "status": "success",
                            "query": query,
                            "results": [
                                {
                                    "title": f"Search result 1 for: {query}",
                                    "url": "https://example.com/1",
                                    "description": f"This is a simulated search result for query: {query}"
                                }
                            ],
                            "message": f"Simulated Brave Search results for: {query}"
                        }
                    # SSE 브리지 테스트 도구가 아닌 경우
                    else:
                        # stdio 백엔드가 있는 경우 프록시
                        if server_config.get('command'):
                            logger.info(f"🔄 Proxying tool call to stdio backend: {name}")
                            
                            from ..services.mcp_connection_service import mcp_connection_service
                            
                            # 실제 MCP 서버로 도구 호출 전달
                            result = await mcp_connection_service.call_tool(
                                server_id=str(server_record.id),  # 실제 서버 ID 사용
                                server_config=server_config,
                                tool_name=name,
                                arguments=arguments,
                                session_id=session_id,
                                project_id=project_id,
                                user_agent=user_agent,
                                ip_address=client_ip,
                                db=tool_log_db
                            )
                        else:
                            # 알 수 없는 도구
                            logger.warning(f"⚠️ Unknown tool called: {name}")
                            result = {
                                "status": "error",
                                "message": f"Unknown tool: {name}"
                            }
                    
                    # 성공 시 세션 통계 업데이트 (successful_calls는 계산된 속성이므로 제거)
                    # total_requests는 이미 위에서 증가시켰음
                    
                    logger.info(f"✅ Tool call result from {server_name}: {result}")
                    
                    # 결과를 TextContent 형식으로 변환
                    if result:
                        result_text = str(result) if not isinstance(result, str) else result
                    else:
                        result_text = f"Tool '{name}' executed successfully"
                    
                    return [
                        types.TextContent(
                            type="text",
                            text=result_text
                        )
                    ]
                    
                except Exception as e:
                    # 실패 시 세션 통계 업데이트
                    if client_session:
                        client_session.failed_requests += 1
                        db.commit()
                    raise
                    
                finally:
                    # 동기 세션 정리 - 확실한 정리 보장
                    if tool_log_db:
                        try:
                            tool_log_db.close()
                            logger.debug(f"Tool log DB session closed for tool: {name}")
                        except Exception as close_error:
                            logger.error(f"Error closing tool log DB session: {close_error}")
                
            except Exception as e:
                logger.error(f"❌ Error calling tool {name} on {server_name}: {e}", exc_info=True)
                
                # SSE 브리지 레벨 에러도 ToolCallLog에 기록
                error_log_db = None  # 세션 변수 초기화
                try:
                    from ..models import ToolCallLog, CallStatus
                    import time
                    
                    # 로그용 데이터베이스 세션
                    error_log_db = get_db_session()
                    try:
                        # 에러 상세 분석
                        error_message = str(e)
                        error_code = "SSE_BRIDGE_ERROR"
                        
                        # MCP 프로토콜 에러 감지
                        if "Invalid request parameters" in error_message:
                            error_code = "INVALID_PARAMETERS"
                        elif "initialization was complete" in error_message:
                            error_code = "INITIALIZATION_INCOMPLETE"
                        elif "Method not found" in error_message:
                            error_code = "METHOD_NOT_FOUND"
                        
                        # ToolCallLog 생성
                        error_log = ToolCallLog(
                            session_id=session_id,
                            server_id=str(server_record.id),
                            project_id=project_id,
                            tool_name=name,
                            tool_namespace=f"{str(server_record.id)}.{name}",
                            input_data={
                                'arguments': arguments,
                                'context': {
                                    'user_agent': user_agent,
                                    'ip_address': client_ip,
                                    'call_time': datetime.utcnow().isoformat(),
                                    'error_location': 'sse_bridge'
                                }
                            },
                            output_data=None,
                            execution_time=0.0,  # 즉시 실패
                            status=CallStatus.ERROR,
                            error_message=error_message,
                            error_code=error_code,
                            user_agent=user_agent,
                            ip_address=client_ip
                        )
                        
                        error_log_db.add(error_log)
                        error_log_db.commit()
                        
                        logger.info(f"📊 SSE Bridge error logged: {name} ({error_code})")
                        
                    except Exception as log_error:
                        logger.error(f"❌ Failed to log SSE bridge error: {log_error}")
                        if error_log_db:
                            try:
                                error_log_db.rollback()  # 롤백 시도
                            except:
                                pass
                    finally:
                        # 에러 로그 DB 세션 확실한 정리
                        if error_log_db:
                            try:
                                error_log_db.close()
                                logger.debug(f"Error log DB session closed for tool: {name}")
                            except Exception as close_error:
                                logger.error(f"Error closing error log DB session: {close_error}")
                        
                except ImportError:
                    logger.warning("Could not import ToolCallLog for error logging")
                
                # 실패 시 세션 통계 업데이트
                if client_session:
                    client_session.failed_requests += 1
                    db.commit()
                
                # 에러 시 에러 메시지 반환
                return [
                    types.TextContent(
                        type="text",
                        text=f"Error executing tool '{name}': {str(e)}"
                    )
                ]
        
        # MCP 서버 실행
        logger.info(f"🏃 Starting MCP server execution for {server_name}")
        
        # 올바른 초기화 옵션 생성
        from mcp.server.models import InitializationOptions
        
        init_options = InitializationOptions(
            server_name=f"mcp-orch-{server_name}",
            server_version="1.0.0",
            capabilities=mcp_server.create_initialization_options().capabilities
        )
        logger.info(f"   Initialization options: {init_options}")
        
        # MCP 서버 실행 (Server.run이 초기화 시퀀스를 자동 처리)
        await mcp_server.run(
            read_stream,
            write_stream,
            init_options
        )
        
        logger.info(f"🎯 MCP server session completed for {server_name}")
        
    except Exception as e:
        logger.error(f"Error in MCP bridge session: {e}")
        raise
        
    finally:
        # 세션 종료 처리 - 안전한 DB 세션 관리
        log_db = None
        try:
            if client_session and db:
                try:
                    client_session.status = 'inactive'
                    client_session.updated_at = datetime.utcnow()
                    db.commit()
                    logger.info(f"🔌 ClientSession {session_id} disconnected")
                    
                    # ServerLog에 연결 종료 이벤트 기록 (별도 DB 세션 사용)
                    # 중요: context manager 대신 try-finally로 확실한 정리 보장
                    from ..services.server_log_service import ServerLogService
                    try:
                        log_db = next(get_db())  # DB 세션 생성
                        log_service = ServerLogService(log_db)
                        log_service.add_log(
                            server_id=server_record.id,
                            project_id=project_id,
                            level=LogLevel.INFO,
                            category=LogCategory.CONNECTION,
                            message=f"Client session ended: {client_type} client disconnected",
                            details={
                                "session_id": session_id,
                                "client_type": client_type,
                                "total_requests": client_session.total_requests,
                                "failed_requests": client_session.failed_requests
                            }
                        )
                        log_db.commit()  # 명시적 commit
                        logger.info(f"📝 Disconnection log recorded for session {session_id}")
                    except Exception as log_error:
                        logger.error(f"Failed to record disconnection log: {log_error}")
                        if log_db:
                            try:
                                log_db.rollback()  # 롤백 시도
                            except:
                                pass
                    finally:
                        # 로그 DB 세션 확실한 정리
                        if log_db:
                            try:
                                log_db.close()
                                logger.debug(f"Log DB session closed for session {session_id}")
                            except Exception as close_error:
                                logger.error(f"Error closing log DB session: {close_error}")
                        
                except Exception as e:
                    logger.error(f"Error updating session on disconnect: {e}")
                    
        finally:
            # 메인 DB 세션 정리
            if db:
                try:
                    db.close()
                    logger.debug(f"Main DB session closed for session {session_id}")
                except Exception as close_error:
                    logger.error(f"Error closing main DB session: {close_error}")


# 이제 python-sdk Server 클래스가 모든 메시지 처리를 담당하므로
# 별도의 메시지 처리 함수는 필요 없음


def _build_server_config_from_db(server: McpServer) -> Optional[Dict[str, Any]]:
    """데이터베이스 서버 모델에서 설정 구성 (기존 로직 재사용)"""
    
    try:
        return {
            'id': server.id,
            'command': server.command,
            'args': server.args or [],
            'env': server.env or {},
            'timeout': server.timeout or 60,
            'transportType': server.transport_type or 'stdio',
            'disabled': not server.is_enabled
        }
    except Exception as e:
        logger.error(f"Error building server config: {e}")
        return None