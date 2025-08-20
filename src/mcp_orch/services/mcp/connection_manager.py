"""
MCP Connection Manager

Responsible for managing connections to MCP servers, including:
- Establishing and maintaining connections
- Connection health monitoring  
- Connection lifecycle management
- Connection pooling and reuse

Extracted from mcp_connection_service.py to follow Single Responsibility Principle.
"""

import asyncio
import json
import logging
import os
from typing import Dict, List, Optional, Any, Union
from datetime import datetime

from .interfaces import IMcpConnectionManager
from .error_handler import McpErrorHandler


logger = logging.getLogger(__name__)


class McpConnection:
    """Represents an active MCP server connection"""
    
    def __init__(self, server_id: str, server_config: Dict, process: asyncio.subprocess.Process):
        self.server_id = server_id
        self.server_config = server_config
        self.process = process
        self.created_at = datetime.now()
        self.last_used_at = datetime.now()
        self.is_healthy = True
        
    def touch(self):
        """Update last used timestamp"""
        self.last_used_at = datetime.now()
    
    async def is_alive(self) -> bool:
        """Check if the underlying process is still alive"""
        # SSE 서버는 process가 없으므로 server_config로 판단
        if self.server_config.get('transport_type') == 'sse':
            # SSE 연결은 항상 "alive"로 간주 (실제 테스트는 요청 시점에)
            return True
        
        # stdio 서버는 process 상태 확인
        if self.process is None:
            return False
        return self.process.returncode is None


class McpConnectionManager(IMcpConnectionManager):
    """
    MCP Connection Manager Implementation
    
    Manages the lifecycle of connections to MCP servers with proper resource cleanup
    and connection health monitoring.
    """
    
    def __init__(self, error_handler: Optional[McpErrorHandler] = None):
        self.active_connections: Dict[str, McpConnection] = {}
        self.error_handler = error_handler or McpErrorHandler()
        
    async def connect(self, server_config: Dict) -> McpConnection:
        """
        Establish connection to MCP server
        
        Args:
            server_config: Server configuration dictionary
            
        Returns:
            McpConnection: Active connection object
            
        Raises:
            ToolExecutionError: If connection fails
        """
        try:
            server_id = server_config.get('id', 'unknown')
            
            # Check if we already have an active connection
            if server_id in self.active_connections:
                existing_conn = self.active_connections[server_id]
                if await existing_conn.is_alive():
                    existing_conn.touch()
                    logger.debug(f"♻️ Reusing existing connection for server {server_id}")
                    return existing_conn
                else:
                    # Clean up dead connection
                    logger.warning(f"🧹 Cleaning up dead connection for server {server_id}")
                    await self._cleanup_connection(server_id)
            
            # Create new connection
            logger.info(f"🔗 Creating new connection for server {server_id}")
            connection = await self._create_new_connection(server_config)
            
            # Store connection for reuse
            self.active_connections[server_id] = connection
            
            return connection
            
        except Exception as e:
            error_msg = f"Failed to connect to MCP server {server_config.get('id', 'unknown')}: {e}"
            logger.error(error_msg)
            raise self.error_handler.create_tool_execution_error(error_msg, "CONNECTION_FAILED", {"server_config": server_config})
    
    async def disconnect(self, connection: McpConnection) -> None:
        """
        Close connection to MCP server
        
        Args:
            connection: Connection to close
        """
        try:
            if connection.server_id in self.active_connections:
                del self.active_connections[connection.server_id]
            
            if connection.process and connection.process.returncode is None:
                logger.info(f"🔌 Disconnecting from server {connection.server_id}")
                
                # Graceful shutdown
                if connection.process.stdin:
                    connection.process.stdin.close()
                
                # Wait for process to terminate gracefully
                try:
                    await asyncio.wait_for(connection.process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning(f"⚠️ Force killing server process {connection.server_id}")
                    connection.process.kill()
                    await connection.process.wait()
                    
        except Exception as e:
            logger.error(f"Error disconnecting from server {connection.server_id}: {e}")
    
    async def test_connection(self, server_config: Dict) -> bool:
        """
        Test if connection to server is possible without establishing persistent connection
        
        Args:
            server_config: Server configuration dictionary
            
        Returns:
            bool: True if connection test succeeds
        """
        try:
            transport_type = server_config.get('transport_type', 'stdio')
            
            # SSE 서버 연결 테스트
            if transport_type == 'sse':
                return await self._test_sse_connection(server_config)
            
            # stdio 서버 연결 테스트 (기존 로직)
            command = server_config.get('command', '')
            args = server_config.get('args', [])
            env = server_config.get('env', {})
            timeout = server_config.get('timeout', 10)
            
            logger.debug(f"🔍 Testing MCP connection: {command} {' '.join(args)}")
            
            if not command:
                logger.warning("❌ No command specified for MCP server")
                return False
            
            # MCP initialization message
            init_message = {
                "jsonrpc": "2.0",
                "id": 1,
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
            
            # Execute process with inherited environment
            full_env = os.environ.copy()
            full_env.update(env)
            
            process = await asyncio.create_subprocess_exec(
                command, *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=full_env
            )
            
            # Send initialization message
            init_json = json.dumps(init_message) + '\n'
            process.stdin.write(init_json.encode())
            await process.stdin.drain()
            
            # Wait for response with timeout
            try:
                stdout_data, stderr_data = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
                
                if process.returncode == 0:
                    # Parse response
                    response_lines = stdout_data.decode().strip().split('\n')
                    logger.debug(f"📥 MCP server response lines: {len(response_lines)}")
                    
                    for line in response_lines:
                        if line.strip():
                            try:
                                response = json.loads(line)
                                if response.get('id') == 1 and 'result' in response:
                                    logger.debug("✅ MCP connection test successful")
                                    return True
                            except json.JSONDecodeError:
                                logger.debug(f"⚠️ Failed to parse JSON: {line[:100]}")
                                continue
                
                logger.debug("❌ MCP connection test failed - no valid response")
                if stderr_data:
                    error_msg = self.error_handler.extract_meaningful_error(stderr_data.decode())
                    logger.debug(f"Error details: {error_msg}")
                
                return False
                
            except asyncio.TimeoutError:
                logger.debug("⏰ MCP connection test timed out")
                process.kill()
                await process.wait()
                return False
                
        except Exception as e:
            logger.error(f"MCP connection test failed: {e}")
            return False
    
    async def _test_sse_connection(self, server_config: Dict) -> bool:
        """
        Test SSE server connection
        
        Args:
            server_config: SSE server configuration with url and headers
            
        Returns:
            bool: True if SSE server is reachable
        """
        try:
            import aiohttp
            
            url = server_config.get('url', '')
            headers = server_config.get('headers', {})
            timeout = server_config.get('timeout', 10)
            
            if not url:
                logger.warning("❌ No URL specified for SSE server")
                return False
            
            logger.info(f"🔍 Testing SSE connection to: {url}")
            
            # SSE 서버는 Server-Sent Events를 사용하므로 
            # 먼저 간단한 GET 요청으로 서버가 응답하는지 확인
            async with aiohttp.ClientSession() as session:
                try:
                    # SSE endpoint는 보통 GET으로 스트림을 열기 때문에 GET 요청 시도
                    async with session.get(
                        url,
                        headers={**headers, 'Accept': 'text/event-stream'},
                        timeout=aiohttp.ClientTimeout(total=timeout)
                    ) as response:
                        # SSE 서버는 보통 200 OK로 응답하고 스트림을 열어둠
                        if response.status in [200, 204]:
                            logger.info(f"✅ SSE connection test successful: {url} (HTTP {response.status})")
                            return True
                        # 405 Method Not Allowed는 서버가 존재하지만 GET을 지원하지 않는 경우
                        elif response.status == 405:
                            logger.info(f"⚠️ SSE server exists but doesn't support GET: {url}")
                            # POST로 재시도
                            return await self._test_sse_connection_with_post(server_config)
                        else:
                            logger.warning(f"❌ SSE connection test failed: HTTP {response.status}")
                            return False
                            
                except asyncio.TimeoutError:
                    logger.warning(f"⏰ SSE connection test timed out: {url}")
                    return False
                except aiohttp.ClientError as e:
                    logger.warning(f"❌ SSE connection test failed: {e}")
                    return False
                    
        except ImportError:
            logger.error("aiohttp is required for SSE connections. Install with: pip install aiohttp")
            return False
        except Exception as e:
            logger.error(f"SSE connection test failed: {e}")
            return False
    
    async def _test_sse_connection_with_post(self, server_config: Dict) -> bool:
        """
        Test SSE server connection with POST (for servers that don't support GET)
        """
        try:
            import aiohttp
            
            url = server_config.get('url', '')
            headers = server_config.get('headers', {})
            timeout = server_config.get('timeout', 10)
            
            logger.info(f"🔍 Testing SSE connection with POST to: {url}")
            
            # MCP 초기화 메시지
            init_message = {
                "jsonrpc": "2.0",
                "id": 1,
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
            
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.post(
                        url, 
                        json=init_message,
                        headers={**headers, 'Content-Type': 'application/json'},
                        timeout=aiohttp.ClientTimeout(total=timeout)
                    ) as response:
                        if response.status in [200, 202, 204]:
                            logger.info(f"✅ SSE POST connection test successful: {url}")
                            return True
                        else:
                            logger.warning(f"❌ SSE POST connection test failed: HTTP {response.status}")
                            return False
                            
                except asyncio.TimeoutError:
                    logger.warning(f"⏰ SSE POST connection test timed out: {url}")
                    return False
                except aiohttp.ClientError as e:
                    logger.warning(f"❌ SSE POST connection test failed: {e}")
                    return False
                    
        except ImportError:
            logger.error("aiohttp is required for SSE connections. Install with: pip install aiohttp")
            return False
        except Exception as e:
            logger.error(f"SSE connection test failed: {e}")
            return False
    
    async def is_connection_alive(self, connection: McpConnection) -> bool:
        """
        Check if existing connection is still alive
        
        Args:
            connection: Connection to check
            
        Returns:
            bool: True if connection is alive and healthy
        """
        try:
            if not connection:
                return False
                
            # Check if process is still running
            if not await connection.is_alive():
                logger.debug(f"💀 Connection process for {connection.server_id} is dead")
                return False
            
            # Could add additional health checks here (ping/heartbeat)
            # For now, just check process status
            connection.touch()
            return True
            
        except Exception as e:
            logger.error(f"Error checking connection health for {connection.server_id}: {e}")
            return False
    
    async def _create_new_connection(self, server_config: Dict) -> McpConnection:
        """Create a new MCP server connection"""
        transport_type = server_config.get('transport_type', 'stdio')
        server_id = server_config.get('id', 'unknown')
        
        # SSE 서버 연결
        if transport_type == 'sse':
            url = server_config.get('url', '')
            if not url:
                raise ValueError("No URL specified for SSE server")
            
            # SSE 연결은 process가 없으므로 None으로 설정
            # 실제 SSE 연결은 요청 시점에 생성됨
            connection = McpConnection(server_id, server_config, None)
            logger.info(f"✅ Created SSE connection configuration for server {server_id}")
            return connection
        
        # stdio 서버 연결 (기존 로직)
        command = server_config.get('command', '')
        args = server_config.get('args', [])
        env = server_config.get('env', {})
        
        if not command:
            raise ValueError("No command specified for stdio server")
        
        # Prepare environment
        full_env = os.environ.copy()
        full_env.update(env)
        
        # Create subprocess
        process = await asyncio.create_subprocess_exec(
            command, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=full_env
        )
        
        # Create connection object
        connection = McpConnection(server_id, server_config, process)
        
        logger.info(f"✅ Created new connection for server {server_id}")
        return connection
    
    async def _cleanup_connection(self, server_id: str) -> None:
        """Clean up a dead or invalid connection"""
        if server_id in self.active_connections:
            connection = self.active_connections[server_id]
            await self.disconnect(connection)
    
    async def cleanup_all_connections(self) -> None:
        """Clean up all active connections (for shutdown)"""
        logger.info("🧹 Cleaning up all MCP connections")
        
        connections_to_cleanup = list(self.active_connections.values())
        for connection in connections_to_cleanup:
            await self.disconnect(connection)
        
        self.active_connections.clear()
        logger.info("✅ All MCP connections cleaned up")