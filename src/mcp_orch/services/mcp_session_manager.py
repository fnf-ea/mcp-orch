"""
MCP 세션 매니저 - 진정한 Resource Connection 구현
MCP Python SDK의 ClientSession 패턴을 따른 지속적 세션 관리
"""

import asyncio
import json
import logging
import time
from typing import Dict, List, Optional, Any, Union, Tuple
from datetime import datetime, timedelta
from uuid import UUID
from dataclasses import dataclass, field
from sqlalchemy.orm import Session

from ..models import McpServer, ToolCallLog, CallStatus, ClientSession, ServerLog, LogLevel, LogCategory
from ..models.mcp_server import McpServerStatus
from ..config import MCPSessionConfig
from .server_status_service import ServerStatusService

logger = logging.getLogger(__name__)


@dataclass
class McpSession:
    """MCP 서버와의 지속적 세션"""
    server_id: str
    process: asyncio.subprocess.Process
    read_stream: asyncio.StreamReader
    write_stream: asyncio.StreamWriter
    session_id: str
    created_at: datetime
    last_used_at: datetime
    tools_cache: Optional[List[Dict]] = None
    is_initialized: bool = False
    initialization_lock: Optional[asyncio.Lock] = None
    _read_buffer: str = ""  # MCP 메시지 읽기용 버퍼
    _message_queue: List[Dict] = field(default_factory=list)  # 순서가 맞지 않는 메시지 임시 저장용


class ToolExecutionError(Exception):
    """도구 실행 에러를 위한 상세 정보를 포함한 예외 클래스"""
    def __init__(self, message: str, error_code: str = "UNKNOWN", details: Dict = None):
        super().__init__(message)
        self.error_code = error_code
        self.details = details or {}
        self.message = message


class McpSessionManager:
    """
    MCP Server Session Manager - Based on MCP Python SDK patterns
    
    Manages persistent connections to MCP servers with configurable timeouts
    and automatic cleanup of expired sessions.
    """
    
    def __init__(self, config: Optional[MCPSessionConfig] = None):
        """
        Initialize MCP Session Manager
        
        Args:
            config: MCP session configuration. If None, uses default values with environment variable support.
        """
        if config is None:
            # Load configuration with environment variable support
            import os
            config = MCPSessionConfig(
                session_timeout_minutes=int(os.getenv('MCP_SESSION_TIMEOUT_MINUTES', '30')),
                cleanup_interval_minutes=int(os.getenv('MCP_SESSION_CLEANUP_INTERVAL_MINUTES', '5'))
            )
            
        self.config = config
        self.sessions: Dict[str, McpSession] = {}
        self.session_timeout = timedelta(minutes=config.session_timeout_minutes)
        self.cleanup_interval = timedelta(minutes=config.cleanup_interval_minutes)
        self._cleanup_task: Optional[asyncio.Task] = None
        self._message_id_counter = 0
        
        logger.info(f"🔧 MCP Session Manager initialized:")
        logger.info(f"   Session timeout: {config.session_timeout_minutes} minutes")
        logger.info(f"   Cleanup interval: {config.cleanup_interval_minutes} minutes")
        
    async def start_manager(self):
        """세션 매니저 시작 - 정리 작업 스케줄링"""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_expired_sessions())
            logger.info("🟢 MCP Session Manager started")
    
    async def stop_manager(self):
        """세션 매니저 중지 - 모든 세션 정리"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            
        # 모든 활성 세션 종료
        for session in list(self.sessions.values()):
            await self._close_session(session)
        self.sessions.clear()
        logger.info("🔴 MCP Session Manager stopped")
    
    def _get_next_message_id(self) -> int:
        """다음 메시지 ID 생성"""
        self._message_id_counter += 1
        return self._message_id_counter
    
    def _resolve_server_id(self, server_id: str) -> Tuple[Optional[UUID], Optional[UUID]]:
        """
        server_id를 해석해서 (project_id, actual_server_id) 튜플 반환
        
        Args:
            server_id: "project_id.server_name" 형식 또는 UUID 문자열
            
        Returns:
            tuple: (project_id, actual_server_id) - 둘 다 UUID 또는 None
        """
        if '.' in server_id:
            try:
                project_id_str, server_name = server_id.split('.', 1)
                project_id = UUID(project_id_str)
                
                # DB에서 실제 서버 ID 조회
                from ..database import get_db
                from ..models import McpServer
                db = next(get_db())
                try:
                    server = db.query(McpServer).filter(
                        McpServer.project_id == project_id,
                        McpServer.name == server_name
                    ).first()
                    if server:
                        logger.debug(f"Resolved server_id {server_id} to project={project_id}, server={server.id}")
                        return project_id, server.id
                    else:
                        logger.warning(f"Server not found for {server_id}")
                        return project_id, None
                finally:
                    db.close()
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse server_id format {server_id}: {e}")
                return None, None
        else:
            # UUID 또는 UUID_server_name 형식 처리
            try:
                # '_'가 포함된 경우 UUID 부분만 추출
                if '_' in server_id:
                    uuid_part = server_id.split('_')[0]
                    actual_server_id = UUID(uuid_part)
                    logger.debug(f"Extracted UUID {uuid_part} from server_id {server_id}")
                else:
                    # 순수 UUID 형식
                    actual_server_id = UUID(server_id)
                return None, actual_server_id
            except (ValueError, TypeError) as e:
                logger.error(f"Cannot convert server_id {server_id} to UUID: {e}")
                return None, None
    
    async def get_or_create_session(self, server_id: str, server_config: Dict) -> McpSession:
        """서버 세션을 가져오거나 새로 생성 (MCP 표준 패턴)"""
        # 기존 세션이 있고 유효한지 확인
        if server_id in self.sessions:
            session = self.sessions[server_id]
            
            # 세션이 살아있는지 확인
            if await self._is_session_alive(session):
                session.last_used_at = datetime.utcnow()
                logger.info(f"♻️ Reusing existing session for server {server_id}")
                return session
            else:
                # 죽은 세션 정리
                logger.warning(f"⚠️ Session for server {server_id} is dead, creating new one")
                await self._close_session(session)
                del self.sessions[server_id]
        
        # 새 세션 생성 (MCP stdio_client 패턴)
        session = await self._create_new_session(server_id, server_config)
        self.sessions[server_id] = session
        logger.info(f"🆕 Created new session for server {server_id}")
        return session
    
    async def _create_new_session(self, server_id: str, server_config: Dict) -> McpSession:
        """새 MCP 세션 생성 - stdio/SSE 패턴 모두 지원"""
        transport_type = server_config.get('transport_type', 'stdio')
        
        if transport_type == 'sse':
            # SSE 서버는 별도 처리 - 더미 세션 객체 생성
            logger.info(f"🌐 Creating SSE session placeholder for server {server_id}")
            # SSE는 process가 없으므로 None으로 설정하고 더미 스트림 사용
            session = McpSession(
                server_id=server_id,
                process=None,  # SSE는 프로세스가 없음
                read_stream=None,  # SSE는 HTTP 기반
                write_stream=None,  # SSE는 HTTP 기반
                session_id=f"sse_{server_id}_{int(time.time())}",
                created_at=datetime.utcnow(),
                last_used_at=datetime.utcnow(),
                is_initialized=True  # SSE는 별도 초기화 불필요
            )
            return session
        
        # stdio 서버 처리 (기존 로직)
        command = server_config.get('command', '')
        args = server_config.get('args', [])
        env = server_config.get('env', {})
        
        if not command:
            raise ValueError(f"Server {server_id} command not configured")
        
        logger.info(f"🚀 Creating new MCP session for server {server_id}")
        logger.info(f"🔍 Command: {command} {' '.join(args)}")
        
        # 환경 변수 설정
        import os
        full_env = os.environ.copy()
        full_env.update(env)
        
        # stdio 서브프로세스 생성 (MCP 표준)
        try:
            process = await asyncio.create_subprocess_exec(
                command, *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=full_env
            )
            logger.info(f"✅ MCP process created with PID: {process.pid}")
        except Exception as e:
            logger.error(f"❌ Failed to create MCP process: {e}")
            raise
        
        # 스트림 래퍼 생성
        read_stream = process.stdout
        write_stream = process.stdin
        
        # 세션 객체 생성
        session = McpSession(
            server_id=server_id,
            process=process,
            read_stream=read_stream,
            write_stream=write_stream,
            session_id=f"session_{server_id}_{int(time.time())}",
            created_at=datetime.utcnow(),
            last_used_at=datetime.utcnow(),
            initialization_lock=asyncio.Lock()
        )
        
        return session
    
    async def initialize_session(self, session: McpSession) -> None:
        """MCP 세션 초기화 - stdio/SSE 모두 지원 (재시도 메커니즘 포함)"""
        if session.is_initialized:
            return
        
        # SSE 세션의 경우 초기화 건너뛰기
        if session.process is None:
            logger.info(f"🌐 SSE session - skipping initialization for server {session.server_id}")
            session.is_initialized = True
            return
            
        async with session.initialization_lock:
            if session.is_initialized:
                return
                
            logger.info(f"🔧 Initializing MCP session for server {session.server_id}")
            
            # 재시도 설정
            max_retries = 3
            base_delay = 1  # 초기 대기 시간 (초)
            
            for attempt in range(max_retries):
                try:
                    if attempt > 0:
                        delay = base_delay * (2 ** (attempt - 1))
                        logger.info(f"⏳ Retrying initialization (attempt {attempt + 1}/{max_retries}) after {delay}s delay...")
                        await asyncio.sleep(delay)
                    
                    # MCP 프로토콜 초기화 메시지
                    init_message = {
                        "jsonrpc": "2.0",
                        "id": self._get_next_message_id(),
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {
                                "roots": {"listChanged": True},
                                "sampling": {}
                            },
                            "clientInfo": {
                                "name": "mcp-orch", 
                                "version": "1.0.0"
                            }
                        }
                    }
                    
                    # 초기화 메시지 전송
                    await self._send_message(session, init_message)
                    
                    # 초기화 응답 대기 (메시지 ID 매칭) - Context7 등 복잡한 서버를 위해 타임아웃 증가
                    init_response = await self._read_message(session, timeout=30, expected_id=init_message['id'])
                    if not init_response or init_response.get('id') != init_message['id']:
                        raise Exception("Failed to receive initialization response")
                    
                    if 'error' in init_response:
                        error_msg = init_response['error'].get('message', 'Unknown error')
                        raise Exception(f"Server initialization failed: {error_msg}")
                    
                    # initialized notification 전송 (MCP 표준)
                    initialized_notification = {
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                        "params": {}
                    }
                    await self._send_message(session, initialized_notification)
                    
                    session.is_initialized = True
                    logger.info(f"✅ MCP session initialized for server {session.server_id} (attempt {attempt + 1})")
                    break  # 성공 시 재시도 루프 종료
                    
                except Exception as e:
                    logger.warning(f"❌ Initialization attempt {attempt + 1} failed for server {session.server_id}: {e}")
                    if attempt == max_retries - 1:
                        # 모든 재시도 실패
                        logger.error(f"💥 All {max_retries} initialization attempts failed for server {session.server_id}")
                        raise e
            
            # 🔄 서버 상태 자동 업데이트: MCP 세션 초기화 성공 시 ACTIVE로 설정
            try:
                # server_id에서 프로젝트 ID 추출 (server_id가 "project_id.server_name" 형태인 경우)
                if '.' in session.server_id:
                    project_id_str, server_name = session.server_id.split('.', 1)
                    project_id = UUID(project_id_str)
                    
                    await ServerStatusService.update_server_status_on_connection(
                        server_id=session.server_id,
                        project_id=project_id,
                        status=McpServerStatus.ACTIVE,
                        connection_type="MCP_SESSION_INIT"
                    )
            except Exception as e:
                logger.error(f"❌ Failed to update server status on MCP session init: {e}")
    
    def _should_retry_error(self, error: Exception) -> str:
        """재시도 가능한 오류인지 확인하고 오류 타입 반환"""
        error_msg = str(error).lower()
        
        # 초기화 관련 오류
        if any(keyword in error_msg for keyword in [
            'initialization', 'initialize', 'before initialization', 
            'not initialized', 'initialization incomplete'
        ]):
            return 'initialization'
        
        # 파라미터 관련 오류
        if any(keyword in error_msg for keyword in [
            'invalid request parameters', 'invalid parameters',
            'parameter error', 'bad request'
        ]):
            return 'parameters'
        
        # 타임아웃 관련 오류
        if any(keyword in error_msg for keyword in [
            'timeout', 'timed out', 'connection timeout'
        ]):
            return 'timeout'
        
        # 연결 관련 오류
        if any(keyword in error_msg for keyword in [
            'connection', 'connect', 'no response', 'read timeout'
        ]):
            return 'connection'
        
        return None  # 재시도 불가능한 오류
    
    async def _wait_before_retry(self, error_type: str, attempt: int):
        """오류 타입별 재시도 대기"""
        delay_maps = {
            'initialization': [2, 4, 8],     # 초기화 오류: 긴 대기
            'parameters': [0.5, 1, 2],       # 파라미터 오류: 짧은 대기
            'timeout': [1, 3, 5],            # 타임아웃 오류: 중간 대기
            'connection': [1, 2, 4],         # 연결 오류: 기본 대기
            'default': [1, 2, 4]             # 기본
        }
        
        delays = delay_maps.get(error_type, delay_maps['default'])
        delay = delays[min(attempt, len(delays) - 1)]
        
        logger.info(f"⏳ Waiting {delay}s before retry (error_type: {error_type}, attempt: {attempt + 1})")
        await asyncio.sleep(delay)
    
    async def call_tool(
        self, 
        server_id: str, 
        server_config: Dict, 
        tool_name: str, 
        arguments: Dict,
        session_id: Optional[str] = None,
        project_id: Optional[Union[str, UUID]] = None,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None,
        db: Optional[Session] = None
    ) -> Dict:
        """MCP 도구 호출 - 재시도 메커니즘 포함"""
        max_retries = 3
        last_error = None
        
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logger.info(f"🔄 Retrying tool call {tool_name} (attempt {attempt + 1}/{max_retries})")
                
                result = await self._call_tool_single(
                    server_id, server_config, tool_name, arguments,
                    session_id, project_id, user_agent, ip_address, db
                )
                
                if attempt > 0:
                    logger.info(f"✅ Tool call {tool_name} succeeded on attempt {attempt + 1}")
                
                return result
                
            except Exception as e:
                last_error = e
                error_type = self._should_retry_error(e)
                
                if error_type and attempt < max_retries - 1:
                    logger.warning(f"❌ Tool call {tool_name} failed (attempt {attempt + 1}): {e}")
                    logger.info(f"🔄 Will retry due to {error_type} error")
                    
                    # 심각한 초기화 오류의 경우 세션 재생성 고려
                    if error_type == 'initialization' and attempt > 0:
                        try:
                            logger.info(f"🔄 Recreating session due to persistent initialization issues")
                            if server_id in self.sessions:
                                await self.close_session(server_id)
                        except Exception as cleanup_error:
                            logger.warning(f"⚠️ Session cleanup failed: {cleanup_error}")
                    
                    await self._wait_before_retry(error_type, attempt)
                    continue
                
                # 재시도 불가능하거나 모든 재시도 실패
                break
        
        # 모든 재시도 실패
        logger.error(f"💥 Tool call {tool_name} failed after {max_retries} attempts")
        raise last_error
    
    async def _call_tool_single(
        self, 
        server_id: str, 
        server_config: Dict, 
        tool_name: str, 
        arguments: Dict,
        session_id: Optional[str] = None,
        project_id: Optional[Union[str, UUID]] = None,
        user_agent: Optional[str] = None,
        ip_address: Optional[str] = None,
        db: Optional[Session] = None
    ) -> Dict:
        """단일 MCP 도구 호출 (재시도 로직 없음) - stdio/SSE 방식 모두 지원"""
        start_time = time.time()
        
        # 프로젝트 ID 변환
        converted_project_id = None
        if project_id:
            try:
                if isinstance(project_id, str):
                    converted_project_id = UUID(project_id)
                else:
                    converted_project_id = project_id
            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid project_id format: {project_id}, error: {e}")
        
        # server_id 해석: "project_id.server_name" 형식 또는 UUID
        resolved_project_id, actual_server_id = self._resolve_server_id(server_id)
        
        # 로그 데이터 준비
        log_data = {
            'server_id': actual_server_id,
            'project_id': converted_project_id,
            'tool_name': tool_name,
            'arguments': arguments,
            'session_id': session_id,
            'user_agent': user_agent,
            'ip_address': ip_address,
            'timestamp': datetime.utcnow()
        }
        
        try:
            logger.info(f"🔧 Calling tool {tool_name} on server {server_id} (MCP Session)")
            
            # 서버가 비활성화된 경우
            if not server_config.get('is_enabled', True):
                raise ValueError(f"Server {server_id} is disabled")
            
            # 🆕 transport_type에 따라 처리 방식 분기
            transport_type = server_config.get('transport_type', 'stdio')
            
            if transport_type == 'sse':
                # SSE 서버는 별도 처리
                logger.info(f"🌐 Calling tool {tool_name} on SSE server {server_id}")
                result = await self._call_sse_tool(server_id, server_config, tool_name, arguments)
            else:
                # stdio 서버는 기존 세션 기반 처리
                logger.info(f"📡 Calling tool {tool_name} on stdio server {server_id}")
                result = await self._call_stdio_tool(server_id, server_config, tool_name, arguments)
            
            execution_time = (time.time() - start_time) * 1000  # 밀리초
            
            # 성공 로그 저장
            if db:
                await self._save_tool_call_log(
                    db, log_data, execution_time, CallStatus.SUCCESS, 
                    {'result': result}
                )
            
            logger.info(f"✅ Tool {tool_name} executed successfully in {execution_time:.2f}ms")
            return result
            
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            if db:
                status = CallStatus.TIMEOUT if "timeout" in str(e).lower() else CallStatus.FAILED
                await self._save_tool_call_log(
                    db, log_data, execution_time, status, 
                    {'error': str(e)}
                )
            logger.error(f"❌ Error calling tool {tool_name} on server {server_id}: {e}")
            raise
    
    async def _call_sse_tool(self, server_id: str, server_config: Dict, tool_name: str, arguments: Dict) -> Dict:
        """SSE 서버 도구 호출"""
        try:
            # SSEMCPServer 인스턴스 생성 및 사용
            from ..core.sse_server import SSEMCPServer, SSEServerConfig
            
            # SSE 서버 설정 생성
            sse_config = SSEServerConfig(
                name=server_id,
                url=server_config.get('url', ''),
                headers=server_config.get('headers', {}),
                timeout=server_config.get('timeout', 30),
                disabled=not server_config.get('is_enabled', True)
            )
            
            # SSE 서버 인스턴스 생성
            sse_server = SSEMCPServer(sse_config)
            
            try:
                # SSE 서버 시작 (초기화 포함)
                await sse_server.start(skip_initialization=False)
                
                # 도구 호출
                result = await sse_server.call_tool(tool_name, arguments)
                logger.info(f"✅ SSE tool call completed: {tool_name}")
                return result
                
            finally:
                # SSE 서버 정리
                if sse_server.is_connected:
                    await sse_server.stop()
                    
        except Exception as e:
            logger.error(f"❌ Error calling SSE tool {tool_name}: {e}")
            raise ToolExecutionError(f"SSE tool execution failed: {e}")
    
    async def _call_stdio_tool(self, server_id: str, server_config: Dict, tool_name: str, arguments: Dict) -> Dict:
        """stdio 서버 도구 호출 (기존 로직)"""
        # 세션 가져오기 또는 생성
        session = await self.get_or_create_session(server_id, server_config)
        
        # 세션 초기화 (필요시)
        await self.initialize_session(session)
        
        # 도구 호출 메시지 생성
        message_id = self._get_next_message_id()
        tool_message = {
            "jsonrpc": "2.0",
            "id": message_id,
            "method": "tools/call",
            "params": {
                "name": tool_name
            }
        }
        
        # arguments가 비어있지 않은 경우에만 추가
        if arguments:
            tool_message["params"]["arguments"] = arguments
        else:
            # 일부 MCP 서버는 빈 arguments를 기대하므로 명시적으로 추가
            tool_message["params"]["arguments"] = {}
        
        logger.info(f"🔧 Sending tool call message: {json.dumps(tool_message)}")
        
        # 메시지 전송
        await self._send_message(session, tool_message)
        logger.info(f"📤 Sent tool call message for {tool_name} (ID: {tool_message['id']})")
        
        # 응답 대기 (메시지 ID 매칭)
        timeout = server_config.get('timeout', 60)
        response = await self._read_message(session, timeout=timeout, expected_id=tool_message['id'])
        
        # 응답 디버깅
        if not response:
            logger.error(f"❌ No response received for tool call {tool_name} (ID: {tool_message['id']})")
            raise ToolExecutionError("No response received from MCP server")
        
        logger.info(f"📥 Received response for {tool_name}: ID={response.get('id')}, expected={tool_message['id']}")
        logger.info(f"📥 Full response content: {json.dumps(response)}")
        
        if response.get('id') != tool_message['id']:
            logger.error(f"❌ Message ID mismatch: expected {tool_message['id']}, got {response.get('id')}")
            raise ToolExecutionError(f"Message ID mismatch: expected {tool_message['id']}, got {response.get('id')}")
        
        if 'error' in response:
            error_msg = response['error'].get('message', 'Unknown error')
            logger.error(f"❌ Tool call error: {error_msg}")
            raise ToolExecutionError(f"Tool execution failed: {error_msg}")
        
        if 'result' not in response:
            raise ToolExecutionError("No result in tool call response")
        
        result = response['result']
        
        # 세션 사용 시간 업데이트
        session.last_used_at = datetime.utcnow()
        
        return result
    
    async def get_server_tools(self, server_id: str, server_config: Dict, project_id: Optional[UUID] = None) -> List[Dict]:
        """서버 도구 목록 조회 - stdio/SSE 방식 모두 지원 + 툴 필터링 적용"""
        try:
            # 🆕 transport_type에 따라 처리 방식 분기
            transport_type = server_config.get('transport_type', 'stdio')
            
            if transport_type == 'sse':
                # SSE 서버는 별도 처리
                return await self._get_sse_server_tools(server_id, server_config, project_id)
            else:
                # stdio 서버는 기존 세션 기반 처리
                return await self._get_stdio_server_tools(server_id, server_config, project_id)
                
        except Exception as e:
            logger.error(f"❌ Error getting tools for server {server_id}: {e}")
            return []
    
    async def _get_sse_server_tools(self, server_id: str, server_config: Dict, project_id: Optional[UUID] = None) -> List[Dict]:
        """SSE 서버 도구 목록 조회"""
        try:
            logger.info(f"🌐 Getting tools from SSE server {server_id}")
            
            # SSEMCPServer 인스턴스 생성 및 사용
            from ..core.sse_server import SSEMCPServer, SSEServerConfig
            
            # SSE 서버 설정 생성
            sse_config = SSEServerConfig(
                name=server_id,
                url=server_config.get('url', ''),
                headers=server_config.get('headers', {}),
                timeout=server_config.get('timeout', 30),
                disabled=not server_config.get('is_enabled', True)
            )
            
            # SSE 서버 인스턴스 생성
            sse_server = SSEMCPServer(sse_config)
            
            try:
                # 🆕 일원화된 SSE 처리: 모든 SSE 서버를 독립적인 외부 서버로 처리
                logger.info(f"🌐 Connecting to SSE server {server_id} at {sse_config.url}")
                
                await sse_server.start(skip_initialization=False)
                tools = sse_server.tools
                logger.info(f"✅ Retrieved {len(tools)} tools from SSE server {server_id}")
                
                # 🆕 project_id 우선 사용: API에서 전달된 project_id가 있으면 우선 사용
                if project_id:
                    # API에서 project_id가 전달된 경우 (외부 SSE 서버)
                    resolved_project_id = project_id
                    actual_server_id = UUID(server_id) if isinstance(server_id, str) else server_id
                    logger.info(f"🔍 [DEBUG] Using provided project_id for SSE server: project_id={resolved_project_id}, server_id={actual_server_id}")
                else:
                    # 기존 server_id 해석 로직 사용 (내부 브리지 서버)
                    resolved_project_id, actual_server_id = self._resolve_server_id(server_id)
                    logger.info(f"🔍 [DEBUG] Resolved from server_id: {server_id} -> project_id={resolved_project_id}, actual_server_id={actual_server_id}")
                
                # 🆕 도구 필터링 적용
                filtered_tools = tools
                if resolved_project_id and actual_server_id:
                    from .tool_filtering_service import ToolFilteringService
                    filtered_tools = await ToolFilteringService.filter_tools_by_preferences(
                        project_id=resolved_project_id,
                        server_id=actual_server_id,
                        tools=tools,
                        db=None  # 세션 매니저에서는 별도 DB 세션 관리
                    )
                    logger.info(f"🎯 Applied filtering to SSE tools: {len(filtered_tools)}/{len(tools)} tools enabled")
                else:
                    logger.warning(f"⚠️ Skipping tool filtering due to missing IDs: project_id={resolved_project_id}, server_id={actual_server_id}")
                
                logger.info(f"✅ Retrieved {len(filtered_tools)} filtered tools from SSE server {server_id}")
                return filtered_tools
                
            finally:
                # SSE 서버 정리
                if sse_server.is_connected:
                    await sse_server.stop()
                
        except Exception as e:
            logger.error(f"❌ Error getting tools from SSE server {server_id}: {e}")
            return []
    
    async def _get_stdio_server_tools(self, server_id: str, server_config: Dict, project_id: Optional[UUID] = None) -> List[Dict]:
        """stdio 서버 도구 목록 조회 (기존 로직)"""
        try:
            # 세션 가져오기 또는 생성
            session = await self.get_or_create_session(server_id, server_config)
            
            # 세션 초기화 (필요시)
            await self.initialize_session(session)
            
            # 🆕 project_id 우선 사용: API에서 전달된 project_id가 있으면 우선 사용
            if project_id:
                # API에서 project_id가 전달된 경우
                resolved_project_id = project_id
                actual_server_id = UUID(server_id) if isinstance(server_id, str) else server_id
                logger.info(f"🔍 [DEBUG] Using provided project_id for stdio server: project_id={resolved_project_id}, server_id={actual_server_id}")
            else:
                # 기존 server_id 해석 로직 사용
                resolved_project_id, actual_server_id = self._resolve_server_id(server_id)
                logger.info(f"🔍 [DEBUG] Resolved from server_id: {server_id} -> project_id={resolved_project_id}, actual_server_id={actual_server_id}")
            
            # 캐시된 도구 목록이 있으면 필터링 후 반환
            if session.tools_cache is not None:
                logger.info(f"📋 Using cached tools for server {server_id}")
                
                # 🆕 캐시된 도구에 실시간 필터링 적용
                if resolved_project_id and actual_server_id:
                    from .tool_filtering_service import ToolFilteringService
                    filtered_tools = await ToolFilteringService.filter_tools_by_preferences(
                        project_id=resolved_project_id,
                        server_id=actual_server_id,
                        tools=session.tools_cache,
                        db=None  # 세션 매니저에서는 별도 DB 세션 관리
                    )
                    logger.info(f"🎯 Applied filtering to cached tools: {len(filtered_tools)}/{len(session.tools_cache)} tools enabled")
                    return filtered_tools
                else:
                    logger.warning(f"⚠️ Skipping cached tool filtering due to missing IDs: project_id={resolved_project_id}, server_id={actual_server_id}")
                
                return session.tools_cache
            
            # 도구 목록 요청
            tools_message = {
                "jsonrpc": "2.0",
                "id": self._get_next_message_id(),
                "method": "tools/list",
                "params": {}
            }
            
            # 메시지 전송
            await self._send_message(session, tools_message)
            
            # 응답 대기 (메시지 ID 매칭)
            response = await self._read_message(session, timeout=30, expected_id=tools_message['id'])
            
            if not response or response.get('id') != tools_message['id']:
                raise Exception("Invalid tools list response")
            
            if 'error' in response:
                error_msg = response['error'].get('message', 'Unknown error')
                raise Exception(f"Tools list failed: {error_msg}")
            
            raw_tools = response.get('result', {}).get('tools', [])
            
            # 도구 데이터 정규화 (기존 구현과 호환성 유지)
            tools = []
            for tool in raw_tools:
                normalized_tool = {
                    'name': tool.get('name', ''),
                    'description': tool.get('description', ''),
                    'schema': tool.get('inputSchema', {})  # inputSchema -> schema 변환
                }
                tools.append(normalized_tool)
            
            # 🆕 새로 조회한 도구에 필터링 적용
            filtered_tools = tools
            if resolved_project_id and actual_server_id:
                from .tool_filtering_service import ToolFilteringService
                filtered_tools = await ToolFilteringService.filter_tools_by_preferences(
                    project_id=resolved_project_id,
                    server_id=actual_server_id,
                    tools=tools,
                    db=None  # 세션 매니저에서는 별도 DB 세션 관리
                )
                logger.info(f"🎯 Applied filtering to new tools: {len(filtered_tools)}/{len(tools)} tools enabled")
            else:
                logger.warning(f"⚠️ Skipping new tool filtering due to missing IDs: project_id={resolved_project_id}, server_id={actual_server_id}")
            
            # 🆕 필터링된 도구를 캐시에 저장 (원본 대신 필터링된 결과)
            session.tools_cache = filtered_tools
            session.last_used_at = datetime.utcnow()
            
            logger.info(f"📋 Retrieved and cached {len(filtered_tools)} filtered tools for server {server_id}")
            return filtered_tools
            
        except Exception as e:
            logger.error(f"❌ Error getting tools for server {server_id}: {e}")
            return []
    
    async def _send_message(self, session: McpSession, message: Dict) -> None:
        """메시지 전송"""
        try:
            message_json = json.dumps(message) + '\n'
            session.write_stream.write(message_json.encode())
            await session.write_stream.drain()
            logger.debug(f"📤 Sent message: {message.get('method', message.get('id'))}")
        except Exception as e:
            logger.error(f"❌ Failed to send message: {e}")
            raise
    
    async def _read_message(self, session: McpSession, timeout: int = 60, expected_id: Optional[int] = None) -> Optional[Dict]:
        """메시지 읽기 - ID 기반 매칭 지원 (UTF-8 안전 처리)"""
        try:
            # 세션에 읽기 버퍼, 바이트 버퍼, 디코더, 메시지 큐가 없으면 초기화
            if not hasattr(session, '_read_buffer'):
                session._read_buffer = ""
            if not hasattr(session, '_byte_buffer'):
                session._byte_buffer = b""
            if not hasattr(session, '_utf8_decoder'):
                import codecs
                session._utf8_decoder = codecs.getincrementaldecoder('utf-8')(errors='strict')
            if not hasattr(session, '_message_queue'):
                session._message_queue = []
            
            # 먼저 큐에서 expected_id와 일치하는 메시지 찾기
            if expected_id is not None:
                for i, queued_message in enumerate(session._message_queue):
                    if queued_message.get('id') == expected_id:
                        # 일치하는 메시지 발견, 큐에서 제거하고 반환
                        return session._message_queue.pop(i)
            
            while True:
                # 완전한 라인이 버퍼에 있는지 먼저 확인
                if '\n' in session._read_buffer:
                    lines = session._read_buffer.split('\n')
                    session._read_buffer = lines.pop()  # 마지막 불완전한 라인은 버퍼에 유지
                    
                    # 모든 완전한 라인 처리
                    for line_text in lines:
                        line_text = line_text.strip()
                        if line_text:
                            try:
                                response = json.loads(line_text)
                                logger.debug(f"📥 Received message ({len(line_text)} bytes): {response.get('method', response.get('id'))}")
                                logger.debug(f"📥 Message content: {response}")
                                
                                # expected_id가 지정되었고 일치하면 즉시 반환
                                if expected_id is not None and response.get('id') == expected_id:
                                    return response
                                # expected_id가 지정되지 않았으면 첫 번째 메시지 반환
                                elif expected_id is None:
                                    return response
                                # ID가 일치하지 않으면 큐에 저장
                                else:
                                    session._message_queue.append(response)
                                    logger.debug(f"📦 Queued message ID {response.get('id')}, waiting for ID {expected_id}")
                                    
                            except json.JSONDecodeError as e:
                                logger.error(f"❌ JSON decode error: {e}")
                                logger.error(f"❌ Invalid JSON content: {line_text[:500]}...")
                                # JSON 파싱 오류는 무시하고 다음 라인 처리
                                continue
                
                # MCP SDK와 동일한 패턴: 청크 기반 읽기 (UTF-8 안전 처리)
                chunk = await asyncio.wait_for(
                    session.read_stream.read(8192),  # 8KB 청크 크기
                    timeout=timeout
                )
                
                if not chunk:
                    # 연결이 닫혔을 때
                    logger.warning("⚠️ Connection closed by MCP server")
                    return None
                
                # 바이트 버퍼에 새 청크 추가
                session._byte_buffer += chunk
                
                # 증분 디코더로 안전하게 UTF-8 디코딩
                try:
                    # 가능한 한 많은 바이트를 디코딩하고, 불완전한 멀티바이트 문자는 버퍼에 유지
                    decoded_text = session._utf8_decoder.decode(session._byte_buffer, final=False)
                    session._byte_buffer = b""  # 성공적으로 디코딩된 바이트는 제거
                    
                    # 디코딩된 텍스트를 문자열 버퍼에 추가
                    session._read_buffer += decoded_text
                    
                except UnicodeDecodeError as decode_error:
                    # 불완전한 멀티바이트 문자가 청크 끝에 있을 경우
                    # 다음 청크를 읽어서 완성할 때까지 바이트 버퍼에 유지
                    logger.debug(f"📦 Incomplete UTF-8 sequence at chunk boundary, buffering: {len(session._byte_buffer)} bytes")
                    continue
            
        except asyncio.TimeoutError:
            logger.error(f"❌ Message read timeout after {timeout} seconds")
            raise ToolExecutionError(f"Message read timeout after {timeout} seconds")
        except json.JSONDecodeError as e:
            logger.error(f"❌ Invalid JSON response: {e}")
            logger.error(f"❌ Raw message: {line_text[:500]}..." if 'line_text' in locals() else "❌ No line_text available")
            raise ToolExecutionError(f"Invalid JSON response: {e}")
        except UnicodeDecodeError as e:
            logger.error(f"❌ Critical UTF-8 encoding error: {e}")
            logger.error(f"❌ Byte buffer length: {len(getattr(session, '_byte_buffer', b''))}")
            logger.error(f"❌ Read buffer length: {len(getattr(session, '_read_buffer', ''))}")
            raise ToolExecutionError(f"Critical UTF-8 encoding error: {e}")
        except Exception as e:
            logger.error(f"❌ Error reading message: {e}")
            raise
    
    async def _is_session_alive(self, session: McpSession) -> bool:
        """세션이 살아있는지 확인 - stdio/SSE 모두 지원"""
        try:
            # SSE 세션의 경우 process가 None이므로 다르게 처리
            if session.process is None:
                # SSE 세션은 항상 "alive"로 간주 (실제 연결 테스트는 요청 시점에)
                return True
            
            # stdio 세션의 경우 프로세스 상태 확인
            if session.process.returncode is not None:
                return False
            
            # 간단한 ping 메시지로 확인 (선택적)
            return True
            
        except Exception:
            return False
    
    async def _close_session(self, session: McpSession) -> None:
        """세션 종료 - stdio/SSE 모두 지원"""
        try:
            logger.info(f"🔴 Closing session for server {session.server_id}")
            
            # SSE 세션의 경우 process가 None이므로 별도 처리
            if session.process is None:
                logger.info(f"🌐 SSE session closed for server {session.server_id}")
            else:
                # stdio 세션의 경우 프로세스 종료
                if session.process.returncode is None:
                    session.process.terminate()
                    try:
                        await asyncio.wait_for(session.process.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        session.process.kill()
                        await session.process.wait()
            
            # 스트림 정리
            if session.write_stream and not session.write_stream.is_closing():
                session.write_stream.close()
                try:
                    await session.write_stream.wait_closed()
                except:
                    pass
            
            # 버퍼 정리
            if hasattr(session, '_read_buffer'):
                session._read_buffer = ""
            if hasattr(session, '_byte_buffer'):
                session._byte_buffer = b""
            if hasattr(session, '_utf8_decoder'):
                session._utf8_decoder = None
            if hasattr(session, '_message_queue'):
                session._message_queue.clear()
            
        except Exception as e:
            logger.error(f"❌ Error closing session for {session.server_id}: {e}")
        
        # 🔄 서버 상태 자동 업데이트: MCP 세션 종료 시 INACTIVE로 설정
        try:
            # server_id에서 프로젝트 ID 추출 (server_id가 "project_id.server_name" 형태인 경우)
            if '.' in session.server_id:
                project_id_str, server_name = session.server_id.split('.', 1)
                project_id = UUID(project_id_str)
                
                await ServerStatusService.update_server_status_on_connection(
                    server_id=session.server_id,
                    project_id=project_id,
                    status=McpServerStatus.INACTIVE,
                    connection_type="MCP_SESSION_CLOSE"
                )
        except Exception as e:
            logger.error(f"❌ Failed to update server status on MCP session close: {e}")
    
    async def _cleanup_expired_sessions(self) -> None:
        """
        Clean up expired sessions (background task)
        
        Runs periodically based on cleanup_interval_minutes configuration
        """
        while True:
            try:
                # Use configured cleanup interval (convert minutes to seconds)
                cleanup_seconds = int(self.cleanup_interval.total_seconds())
                await asyncio.sleep(cleanup_seconds)
                
                now = datetime.utcnow()
                expired_sessions = []
                
                for server_id, session in self.sessions.items():
                    if now - session.last_used_at > self.session_timeout:
                        expired_sessions.append(server_id)
                
                for server_id in expired_sessions:
                    session = self.sessions.pop(server_id, None)
                    if session:
                        await self._close_session(session)
                        logger.info(f"🧹 Cleaned up expired session for server {server_id}")
                        
                        # 🔄 만료된 세션에 대한 추가 상태 업데이트
                        try:
                            if '.' in server_id:
                                project_id_str, server_name = server_id.split('.', 1)
                                project_id = UUID(project_id_str)
                                
                                await ServerStatusService.update_server_status_on_connection(
                                    server_id=server_id,
                                    project_id=project_id,
                                    status=McpServerStatus.INACTIVE,
                                    connection_type="MCP_SESSION_EXPIRED"
                                )
                        except Exception as e:
                            logger.error(f"❌ Failed to update server status on session expiry: {e}")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Error during session cleanup: {e}")
    
    async def _save_tool_call_log(
        self,
        db: Session,
        log_data: Dict,
        execution_time: float,
        status: CallStatus,
        output_data: Optional[Dict] = None,
        error_message: Optional[str] = None,
        error_code: Optional[str] = None
    ):
        """ToolCallLog 데이터베이스에 저장"""
        try:
            # 데이터베이스 세션 타입 검증
            if db is None:
                logger.warning("⚠️ Database session is None, skipping ToolCallLog save")
                return
            
            if not hasattr(db, 'add') or not hasattr(db, 'commit') or not hasattr(db, 'rollback'):
                logger.error(f"❌ Invalid database session type: {type(db)}, expected SQLAlchemy Session")
                return
            
            # 저장할 데이터 로깅
            logger.info(f"🔍 Saving ToolCallLog: server_id={log_data.get('server_id')} (type: {type(log_data.get('server_id'))}), project_id={log_data.get('project_id')}, tool={log_data.get('tool_name')}")
            
            tool_call_log = ToolCallLog(
                session_id=log_data.get('session_id'),
                server_id=log_data.get('server_id'),
                project_id=log_data.get('project_id'),
                tool_name=log_data.get('tool_name'),
                tool_namespace=f"{log_data.get('server_id')}.{log_data.get('tool_name')}",
                arguments=log_data.get('arguments'),
                result=output_data.get('result') if output_data else None,
                error_message=error_message or (output_data.get('error') if output_data else None),
                error_code=error_code,
                execution_time_ms=int(execution_time),  # 밀리초 단위로 저장 (DB 스키마에 맞춰)
                status=status,
                user_agent=log_data.get('user_agent'),
                ip_address=log_data.get('ip_address'),
                created_at=log_data.get('timestamp')
            )
            
            db.add(tool_call_log)
            db.commit()
            
            logger.info(f"✅ ToolCallLog saved successfully: id={tool_call_log.id}, server_id={tool_call_log.server_id}, project_id={tool_call_log.project_id}, tool={tool_call_log.tool_name} ({status.value}) in {execution_time:.3f}ms")
            
        except Exception as e:
            logger.error(f"❌ Failed to save ToolCallLog: {e}")
            logger.error(f"❌ Log data: {log_data}")
            logger.error(f"❌ Output data: {output_data}")
            db.rollback()


# 글로벌 세션 매니저 인스턴스
_session_manager: Optional[McpSessionManager] = None

async def get_session_manager(config: Optional[MCPSessionConfig] = None) -> McpSessionManager:
    """
    Get global session manager instance
    
    Args:
        config: MCP session configuration. If None, uses environment variables or defaults.
    
    Returns:
        McpSessionManager: The global session manager instance
    """
    global _session_manager
    if _session_manager is None:
        _session_manager = McpSessionManager(config)
        await _session_manager.start_manager()
    return _session_manager

async def shutdown_session_manager():
    """글로벌 세션 매니저 종료"""
    global _session_manager
    if _session_manager is not None:
        await _session_manager.stop_manager()
        _session_manager = None