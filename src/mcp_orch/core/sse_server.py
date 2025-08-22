"""
SSE 방식 MCP 서버 관리

HTTP URL을 통해 SSE 방식으로 연결되는 MCP 서버를 관리합니다.
"""

import asyncio
import json
import logging
from typing import Any, Dict, Optional, List, AsyncGenerator
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from httpx_sse import aconnect_sse

from ..config_parser import MCPServerConfig

logger = logging.getLogger(__name__)


@dataclass
class SSEServerConfig:
    """SSE MCP 서버 설정"""
    name: str
    url: str  # SSE 엔드포인트 URL
    type: str = "sse"  # 서버 타입 구분
    timeout: int = 30
    headers: Dict[str, str] = field(default_factory=dict)
    disabled: bool = False
    auto_approve: List[str] = field(default_factory=list)
    
    @classmethod
    def from_dict(cls, name: str, config_dict: Dict[str, Any]) -> "SSEServerConfig":
        """딕셔너리에서 SSE 서버 설정 생성"""
        return cls(
            name=name,
            url=config_dict["url"],
            type=config_dict.get("type", "sse"),
            timeout=config_dict.get("timeout", 30),
            headers=config_dict.get("headers", {}),
            disabled=config_dict.get("disabled", False),
            auto_approve=config_dict.get("auto_approve", [])
        )


@dataclass
class SSEMCPServer:
    """SSE 방식 MCP 서버 인스턴스"""
    config: SSEServerConfig
    http_client: Optional[httpx.AsyncClient] = None
    sse_task: Optional[asyncio.Task] = None
    message_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    request_id: int = 0
    pending_requests: Dict[str, asyncio.Future] = field(default_factory=dict)
    tools: List[Dict[str, Any]] = field(default_factory=list)
    is_connected: bool = False
    is_initialized: bool = False  # 초기화 완료 상태 추적
    session_id: Optional[str] = None
    message_endpoint: Optional[str] = None
    
    async def start(self, skip_initialization: bool = False) -> None:
        """SSE 서버 연결 시작
        
        Args:
            skip_initialization: True인 경우 초기화 및 도구 목록 조회를 건너뜁니다 (브리지 모드용)
        """
        if self.is_connected:
            logger.warning(f"SSE server {self.config.name} is already connected")
            return
            
        logger.info(f"Starting SSE MCP server {self.config.name}: {self.config.url}")
        
        try:
            # HTTP 클라이언트 생성
            self.http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout),
                headers=self.config.headers
            )
            
            # SSE 연결 시작
            self.sse_task = asyncio.create_task(self._sse_connection_loop())
            
            # 연결 대기 (최대 5초)
            for _ in range(50):  # 0.1초씩 50회 = 5초
                if self.is_connected and self.message_endpoint:
                    break
                await asyncio.sleep(0.1)
            
            if not self.is_connected:
                raise ConnectionError(f"Failed to establish SSE connection to {self.config.url}")
            
            # 브리지 모드가 아닌 경우에만 초기화 수행
            if not skip_initialization:
                # 서버 초기화
                await self._initialize()
                
                # 초기화 후 서버가 안정화될 때까지 대기
                await asyncio.sleep(1.0)
                
                # 도구 목록 조회
                await self._list_tools()
                
                logger.info(f"SSE MCP server {self.config.name} connected successfully with {len(self.tools)} tools")
            else:
                logger.info(f"SSE MCP server {self.config.name} connected in bridge mode (skipping initialization)")
            
        except Exception as e:
            logger.error(f"Failed to start SSE MCP server {self.config.name}: {e}")
            await self.stop()
            raise
    
    async def stop(self) -> None:
        """SSE 서버 연결 종료"""
        logger.info(f"Stopping SSE MCP server {self.config.name}")
        
        if self.sse_task:
            self.sse_task.cancel()
            try:
                await self.sse_task
            except asyncio.CancelledError:
                pass
        
        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None
        
        self.is_connected = False
        self.is_initialized = False  # 초기화 상태도 리셋
        self.tools.clear()
        self.session_id = None
        self.message_endpoint = None
        
        # pending requests 정리
        await self._cleanup_pending_requests("Server stopped")
    
    async def _sse_connection_loop(self) -> None:
        """SSE 연결 루프"""
        try:
            async with aconnect_sse(
                self.http_client, 
                "GET", 
                self.config.url,
                headers=self.config.headers
            ) as event_source:
                logger.info(f"SSE connection established to {self.config.url}")
                
                async for sse_event in event_source.aiter_sse():
                    await self._handle_sse_event(sse_event)
                    
        except asyncio.CancelledError:
            logger.info(f"SSE connection cancelled for {self.config.name}")
            raise
        except Exception as e:
            logger.error(f"SSE connection error for {self.config.name}: {e}")
            self.is_connected = False
            raise
        finally:
            await self._cleanup_pending_requests("SSE connection terminated")
    
    async def _handle_sse_event(self, event) -> None:
        """SSE 이벤트 처리"""
        try:
            if event.event == "endpoint":
                # 메시지 엔드포인트 수신
                self.message_endpoint = event.data.strip()
                self.is_connected = True
                logger.debug(f"Received message endpoint: {self.message_endpoint}")
                
            elif event.event == "message" or not event.event:  # 일반 메시지
                data = json.loads(event.data)
                await self._handle_message(data)
                
            else:
                logger.debug(f"Unknown SSE event: {event.event}")
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse SSE event data: {e}")
        except Exception as e:
            logger.error(f"Error handling SSE event: {e}")
    
    async def _handle_message(self, data: Dict[str, Any]) -> None:
        """메시지 처리 (stdio 버전과 동일)"""
        # 응답 메시지인 경우
        if "id" in data and str(data["id"]) in self.pending_requests:
            request_id = str(data["id"])
            future = self.pending_requests.pop(request_id)
            
            if "error" in data:
                future.set_exception(Exception(data["error"].get("message", "Unknown error")))
            else:
                future.set_result(data.get("result"))
        else:
            # 알림 메시지 등은 로깅
            logger.debug(f"Notification from {self.config.name}: {data}")
    
    async def _send_request(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: Optional[int] = None) -> Any:
        """HTTP POST를 통한 요청 전송"""
        if not self.is_connected or not self.message_endpoint:
            raise RuntimeError(f"SSE server {self.config.name} is not connected")
        
        self.request_id += 1
        request_id = str(self.request_id)
        
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": request_id
        }
        
        # Future 생성
        future = asyncio.Future()
        self.pending_requests[request_id] = future
        
        # POST 요청으로 메시지 전송
        try:
            # message_endpoint가 상대 경로인 경우 절대 URL로 변환
            if self.message_endpoint.startswith('/'):
                base_url = f"{urlparse(self.config.url).scheme}://{urlparse(self.config.url).netloc}"
                post_url = urljoin(base_url, self.message_endpoint)
            else:
                post_url = self.message_endpoint
            
            response = await self.http_client.post(
                post_url,
                json=message,
                headers=self.config.headers
            )
            response.raise_for_status()
            
        except Exception as e:
            self.pending_requests.pop(request_id, None)
            raise RuntimeError(f"Failed to send message to {self.config.name}: {e}")
        
        # 응답 대기
        request_timeout = timeout if timeout is not None else self.config.timeout
        try:
            result = await asyncio.wait_for(future, timeout=request_timeout)
            return result
        except asyncio.TimeoutError:
            self.pending_requests.pop(request_id, None)
            raise TimeoutError(f"Request timeout for {method} (waited {request_timeout}s)")
    
    async def _initialize(self) -> None:
        """서버 초기화 (재시도 로직 포함)"""
        if self.is_initialized:
            logger.debug(f"SSE MCP server {self.config.name} already initialized")
            return
            
        logger.info(f"Initializing SSE MCP server {self.config.name}")
        
        # 재시도 로직: 최대 3회, 지수 백오프
        max_retries = 3
        retry_delay = 1.0  # 초기 대기 시간
        
        for attempt in range(max_retries):
            try:
                # 초기화 전 대기 (서버가 준비될 시간 제공)
                if attempt > 0:
                    logger.info(f"Waiting {retry_delay}s before retry...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # 지수 백오프
                
                result = await self._send_request("initialize", {
                    "protocolVersion": "2024-11-05",  # 최신 프로토콜 버전 사용
                    "capabilities": {},
                    "clientInfo": {
                        "name": "mcp-orch",
                        "version": "1.0.0"
                    }
                })
                
                logger.debug(f"Initialize response from {self.config.name}: {result}")
                
                # 'initialized' notification 전송 (SSE는 HTTP POST로 전송)
                await self._send_notification("notifications/initialized", {})
                logger.info(f"Sent initialized notification to {self.config.name}")
                
                self.is_initialized = True
                
                # 초기화 완료 후 안정화 대기
                await asyncio.sleep(0.5)
                
                return
                
            except Exception as e:
                error_msg = str(e)
                
                # 초기화 미완료 에러인 경우 재시도
                if "initialization was complete" in error_msg.lower() or "not initialized" in error_msg.lower():
                    if attempt < max_retries - 1:
                        logger.warning(f"Initialization not ready for {self.config.name} (attempt {attempt + 1}/{max_retries})")
                        continue
                    else:
                        logger.error(f"Failed to initialize {self.config.name} after {max_retries} attempts")
                        raise
                else:
                    # 다른 에러는 즉시 실패
                    logger.error(f"Initialization failed for {self.config.name}: {e}")
                    raise
    
    async def _send_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """알림 메시지 전송 (응답 대기 없음)"""
        if not self.is_connected or not self.message_endpoint:
            raise RuntimeError(f"SSE server {self.config.name} is not connected")
        
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {}
            # notification은 id가 없음
        }
        
        try:
            # message_endpoint가 상대 경로인 경우 절대 URL로 변환
            if self.message_endpoint.startswith('/'):
                base_url = f"{urlparse(self.config.url).scheme}://{urlparse(self.config.url).netloc}"
                post_url = urljoin(base_url, self.message_endpoint)
            else:
                post_url = self.message_endpoint
            
            response = await self.http_client.post(
                post_url,
                json=message,
                headers=self.config.headers
            )
            response.raise_for_status()
            logger.debug(f"Notification sent: {method}")
            
        except Exception as e:
            logger.error(f"Failed to send notification to {self.config.name}: {e}")
    
    async def _list_tools(self) -> None:
        """도구 목록 조회 (재시도 로직 포함)"""
        logger.info(f"Listing tools for SSE MCP server {self.config.name}")
        
        # 초기화 확인
        if not self.is_initialized:
            logger.warning(f"Server {self.config.name} not initialized, initializing first")
            await self._initialize()
        
        # 재시도 로직: 최대 3회
        max_retries = 3
        retry_delay = 0.5  # 초기 대기 시간
        
        for attempt in range(max_retries):
            try:
                result = await self._send_request("tools/list")
                self.tools = result.get("tools", [])
                
                logger.info(f"Found {len(self.tools)} tools in {self.config.name}")
                for tool in self.tools:
                    logger.debug(f"  - {tool['name']}: {tool.get('description', 'No description')}")
                return
                
            except Exception as e:
                error_msg = str(e)
                
                # 초기화 관련 에러인 경우 재시도
                if "initialization" in error_msg.lower() or "not initialized" in error_msg.lower():
                    if attempt < max_retries - 1:
                        logger.warning(f"Tools list failed due to initialization for {self.config.name}, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})")
                        # 초기화 재시도
                        self.is_initialized = False
                        await self._initialize()
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2  # 지수 백오프
                        continue
                    else:
                        logger.error(f"Failed to list tools for {self.config.name} after {max_retries} attempts")
                        self.tools = []  # 실패 시 빈 목록
                        raise
                else:
                    # 다른 에러는 즉시 실패
                    logger.error(f"Failed to list tools for {self.config.name}: {e}")
                    self.tools = []  # 실패 시 빈 목록
                    raise
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """도구 호출 (stdio 버전과 동일)"""
        if not self.is_connected:
            raise RuntimeError(f"SSE MCP server {self.config.name} is not connected")
            
        # auto_approve 체크 제거 - SSE 브릿지에서는 항상 실행
        # logger.debug(f"Calling tool {tool_name} without approval check")
        
        return await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })
    
    def get_namespaced_tools(self) -> List[Dict[str, Any]]:
        """네임스페이스가 적용된 도구 목록 반환 (stdio 버전과 동일)"""
        namespaced_tools = []
        for tool in self.tools:
            namespaced_tool = tool.copy()
            # 원본 이름 저장
            namespaced_tool["original_name"] = tool["name"]
            # 네임스페이스 적용
            namespaced_tool["name"] = f"{self.config.name}.{tool['name']}"
            namespaced_tool["server"] = self.config.name
            namespaced_tools.append(namespaced_tool)
        return namespaced_tools
    
    async def _cleanup_pending_requests(self, error_message: str) -> None:
        """모든 pending requests를 에러로 정리 (stdio 버전과 동일)"""
        if self.pending_requests:
            logger.warning(f"Cleaning up {len(self.pending_requests)} pending requests for {self.config.name}: {error_message}")
            
            # 모든 pending requests를 실패 처리
            for request_id, future in list(self.pending_requests.items()):
                if not future.done():
                    future.set_exception(Exception(f"Connection lost: {error_message}"))
            
            self.pending_requests.clear()
