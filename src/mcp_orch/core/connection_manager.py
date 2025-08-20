"""
통합 연결 관리자

stdio와 SSE 방식 MCP 서버를 통합 관리하는 연결 관리자
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Union
from enum import Enum

from ..proxy.mcp_server import MCPServer
from .sse_server import SSEMCPServer, SSEServerConfig
from ..config_parser import MCPServerConfig

logger = logging.getLogger(__name__)


class ConnectionType(str, Enum):
    """연결 타입"""
    STDIO = "stdio"
    SSE = "sse"


class ServerConnectionInfo:
    """서버 연결 정보"""
    def __init__(self, name: str, connection_type: ConnectionType, server_instance: Union[MCPServer, SSEMCPServer]):
        self.name = name
        self.connection_type = connection_type
        self.server_instance = server_instance
        self.is_connected = False
        self.tools_count = 0
        self.last_error = None
    
    def update_status(self, is_connected: bool, tools_count: int = 0, error: str = None):
        """연결 상태 업데이트"""
        self.is_connected = is_connected
        self.tools_count = tools_count
        self.last_error = error


class UnifiedConnectionManager:
    """stdio와 SSE 방식을 통합 관리하는 연결 관리자"""
    
    def __init__(self):
        self.connections: Dict[str, ServerConnectionInfo] = {}
        self.connection_pools = {
            ConnectionType.STDIO: {},
            ConnectionType.SSE: {}
        }
        self._lock = asyncio.Lock()
    
    async def add_stdio_server(self, name: str, config: MCPServerConfig) -> bool:
        """stdio 방식 MCP 서버 추가"""
        async with self._lock:
            if name in self.connections:
                logger.warning(f"Server {name} already exists")
                return False
            
            try:
                # MCPServer 인스턴스 생성
                server = MCPServer(config)
                connection_info = ServerConnectionInfo(name, ConnectionType.STDIO, server)
                
                self.connections[name] = connection_info
                self.connection_pools[ConnectionType.STDIO][name] = server
                
                logger.info(f"Added stdio MCP server: {name}")
                return True
                
            except Exception as e:
                logger.error(f"Failed to add stdio server {name}: {e}")
                return False
    
    async def add_sse_server(self, name: str, config: SSEServerConfig) -> bool:
        """SSE 방식 MCP 서버 추가"""
        async with self._lock:
            if name in self.connections:
                logger.warning(f"Server {name} already exists")
                return False
            
            try:
                # SSEMCPServer 인스턴스 생성
                server = SSEMCPServer(config)
                connection_info = ServerConnectionInfo(name, ConnectionType.SSE, server)
                
                self.connections[name] = connection_info
                self.connection_pools[ConnectionType.SSE][name] = server
                
                logger.info(f"Added SSE MCP server: {name}")
                return True
                
            except Exception as e:
                logger.error(f"Failed to add SSE server {name}: {e}")
                return False
    
    async def connect_server(self, name: str) -> bool:
        """서버 연결 시작"""
        if name not in self.connections:
            logger.error(f"Server {name} not found")
            return False
        
        connection_info = self.connections[name]
        try:
            await connection_info.server_instance.start()
            
            # 연결 상태 업데이트
            tools_count = len(connection_info.server_instance.tools)
            connection_info.update_status(True, tools_count)
            
            logger.info(f"Connected to {connection_info.connection_type} server {name} ({tools_count} tools)")
            return True
            
        except Exception as e:
            connection_info.update_status(False, 0, str(e))
            logger.error(f"Failed to connect to server {name}: {e}")
            return False
    
    async def disconnect_server(self, name: str) -> bool:
        """서버 연결 해제"""
        if name not in self.connections:
            logger.error(f"Server {name} not found")
            return False
        
        connection_info = self.connections[name]
        try:
            await connection_info.server_instance.stop()
            connection_info.update_status(False, 0)
            
            logger.info(f"Disconnected from {connection_info.connection_type} server {name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to disconnect from server {name}: {e}")
            return False
    
    async def connect_all_servers(self) -> Dict[str, bool]:
        """모든 서버 연결 시작"""
        results = {}
        
        for name in self.connections:
            success = await self.connect_server(name)
            results[name] = success
        
        connected_count = sum(1 for success in results.values() if success)
        total_count = len(results)
        
        logger.info(f"Connected {connected_count}/{total_count} servers")
        return results
    
    async def disconnect_all_servers(self) -> Dict[str, bool]:
        """모든 서버 연결 해제"""
        results = {}
        
        for name in self.connections:
            success = await self.disconnect_server(name)
            results[name] = success
        
        logger.info(f"Disconnected all servers")
        return results
    
    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """도구 호출 (연결 타입에 관계없이 동일한 인터페이스)"""
        if server_name not in self.connections:
            raise ValueError(f"Server {server_name} not found")
        
        connection_info = self.connections[server_name]
        if not connection_info.is_connected:
            raise RuntimeError(f"Server {server_name} is not connected")
        
        try:
            result = await connection_info.server_instance.call_tool(tool_name, arguments)
            return result
            
        except Exception as e:
            connection_info.update_status(connection_info.is_connected, connection_info.tools_count, str(e))
            raise
    
    def get_all_tools(self) -> List[Dict[str, Any]]:
        """모든 서버의 도구 목록 조회 (네임스페이스 포함)"""
        all_tools = []
        
        for name, connection_info in self.connections.items():
            if connection_info.is_connected:
                namespaced_tools = connection_info.server_instance.get_namespaced_tools()
                for tool in namespaced_tools:
                    # 연결 타입 정보 추가
                    tool["connection_type"] = connection_info.connection_type.value
                    tool["namespace"] = f"{name}.{tool['original_name']}"
                all_tools.extend(namespaced_tools)
        
        return all_tools
    
    def get_servers(self) -> Dict[str, Dict[str, Any]]:
        """모든 서버 상태 조회"""
        servers_status = {}
        
        for name, connection_info in self.connections.items():
            servers_status[name] = {
                "name": name,
                "connection_type": connection_info.connection_type.value,
                "is_connected": connection_info.is_connected,
                "tools_count": connection_info.tools_count,
                "last_error": connection_info.last_error
            }
        
        return servers_status
    
    def get_connected_servers(self) -> List[str]:
        """연결된 서버 목록"""
        return [name for name, info in self.connections.items() if info.is_connected]
    
    def get_disconnected_servers(self) -> List[str]:
        """연결되지 않은 서버 목록"""
        return [name for name, info in self.connections.items() if not info.is_connected]
    
    def get_server_by_name(self, name: str) -> Optional[Union[MCPServer, SSEMCPServer]]:
        """이름으로 서버 인스턴스 조회"""
        if name in self.connections:
            return self.connections[name].server_instance
        return None
    
    def get_server_connection_type(self, name: str) -> Optional[ConnectionType]:
        """서버의 연결 타입 조회"""
        if name in self.connections:
            return self.connections[name].connection_type
        return None
    
    async def reload_server(self, name: str) -> bool:
        """서버 재시작 (설정 변경 후 리로드)"""
        if name not in self.connections:
            logger.error(f"Server {name} not found")
            return False
        
        logger.info(f"Reloading server {name}")
        
        # 기존 연결 해제
        await self.disconnect_server(name)
        
        # 잠시 대기
        await asyncio.sleep(1)
        
        # 재연결
        return await self.connect_server(name)
    
    async def health_check(self) -> Dict[str, Any]:
        """전체 연결 상태 헬스체크"""
        total_servers = len(self.connections)
        connected_servers = len(self.get_connected_servers())
        disconnected_servers = len(self.get_disconnected_servers())
        
        total_tools = sum(info.tools_count for info in self.connections.values() if info.is_connected)
        
        # 연결 타입별 통계
        stdio_servers = len([info for info in self.connections.values() if info.connection_type == ConnectionType.STDIO])
        sse_servers = len([info for info in self.connections.values() if info.connection_type == ConnectionType.SSE])
        
        stdio_connected = len([info for info in self.connections.values() 
                              if info.connection_type == ConnectionType.STDIO and info.is_connected])
        sse_connected = len([info for info in self.connections.values() 
                           if info.connection_type == ConnectionType.SSE and info.is_connected])
        
        return {
            "total_servers": total_servers,
            "connected_servers": connected_servers,
            "disconnected_servers": disconnected_servers,
            "total_tools": total_tools,
            "connection_types": {
                "stdio": {
                    "total": stdio_servers,
                    "connected": stdio_connected
                },
                "sse": {
                    "total": sse_servers,
                    "connected": sse_connected
                }
            },
            "server_details": self.get_servers()
        }
