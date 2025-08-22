"""
MCP Protocol Handler for Unified MCP Transport

Handles MCP protocol messages including initialization, tool listing,
and tool execution across multiple servers.
"""

import logging
from typing import Dict, Any, List, Optional
from uuid import UUID

from fastapi.responses import JSONResponse

from ....services.mcp_connection_service import mcp_connection_service
from ....services.tool_filtering_service import ToolFilteringService
from ....utils.namespace import create_namespaced_name
from .health_monitor import ServerHealthInfo, classify_error


logger = logging.getLogger(__name__)


class UnifiedProtocolHandler:
    """Handles MCP protocol operations for unified transport"""
    
    def __init__(self, transport):
        """
        Initialize protocol handler with reference to transport
        
        Args:
            transport: UnifiedMCPTransport instance
        """
        self.transport = transport
        
    async def handle_initialize(self, message: Dict[str, Any]) -> JSONResponse:
        """
        Handle initialization request for unified server
        
        Queues response through SSE message queue instead of direct return.
        """
        request_id = message.get("id")
        params = message.get("params", {})
        
        logger.info(f"🎯 Unified MCP initialize: session={self.transport.session_id}, servers={len(self.transport.project_servers)}")
        
        # Count active servers
        active_servers = [s for s in self.transport.project_servers if s.is_enabled]
        
        # MCP standard initialization response
        response_data = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {
                    "experimental": {},
                    "tools": {
                        "listChanged": False
                    } if active_servers else {},
                    "logging": {},
                    "prompts": {},
                    "resources": {}
                },
                "serverInfo": {
                    "name": f"mcp-orch-unified",
                    "version": "1.9.4"
                },
                "instructions": f"MCP Orchestrator unified proxy for project {self.transport.project_id}."
            }
        }
        
        # Queue response for SSE delivery
        logger.info(f"📤 Queueing initialize response for Unified SSE session {self.transport.session_id}")
        await self.transport.message_queue.put(response_data)
        
        logger.info(f"✅ Unified initialize response queued: session={self.transport.session_id}")
        
        # Return HTTP 202 Accepted (actual response sent via SSE)
        return JSONResponse(content={"status": "processing"}, status_code=202)
    
    async def handle_tools_list(self, message: Dict[str, Any]) -> JSONResponse:
        """
        List tools from all active servers with namespacing and filtering
        """
        all_tools = []
        failed_servers = []
        active_servers = [s for s in self.transport.project_servers if s.is_enabled]
        
        request_id = message.get("id")
        legacy_mode = getattr(self.transport, '_legacy_mode', True)
        
        logger.info(f"📋 Listing unified tools from {len(active_servers)} servers (legacy_mode: {legacy_mode})")
        
        # Collect tools from each server
        for server in active_servers:
            try:
                # Check server health
                if not self.transport._is_server_available(server.name):
                    logger.debug(f"Skipping unavailable server: {server.name}")
                    failed_servers.append(server.name)
                    continue
                
                # Build server config
                server_config = self.transport._build_server_config_for_server(server)
                if not server_config:
                    error_msg = f"Failed to build config for server: {server.name}"
                    logger.warning(error_msg)
                    self.transport._record_server_failure(server.name, Exception(error_msg))
                    failed_servers.append(server.name)
                    continue
                
                # Get tools from server
                tools = await mcp_connection_service.get_server_tools(
                    str(server.id), server_config, project_id=str(self.transport.project_id)
                )
                
                if tools is None:
                    error_msg = f"No tools returned from server: {server.name}"
                    logger.warning(error_msg)
                    self.transport._record_server_failure(server.name, Exception(error_msg))
                    failed_servers.append(server.name)
                    continue
                
                # Apply tool filtering
                filtered_tools = await ToolFilteringService.filter_tools_by_preferences(
                    project_id=self.transport.project_id,
                    server_id=server.id,
                    tools=tools,
                    db=None
                )
                
                logger.info(f"🎯 Applied tool filtering for {server.name}: {len(filtered_tools)}/{len(tools)} tools enabled")
                
                # Process tools with namespacing
                for tool in filtered_tools:
                    try:
                        namespaced_tool = self._create_namespaced_tool(tool, server)
                        all_tools.append(namespaced_tool)
                    except Exception as tool_error:
                        logger.error(f"Error processing tool {tool.get('name', 'unknown')}: {tool_error}")
                
                # Record success
                self.transport._record_server_success(server.name, len(filtered_tools))
                
            except Exception as e:
                logger.error(f"❌ Failed to get tools from server {server.name}: {e}")
                failed_servers.append(server.name)
                self.transport._record_server_failure(server.name, e)
        
        # Note: Orchestrator meta-tools removed per user request
        # Only show actual MCP server tools
        
        # Log summary
        logger.info(f"✅ Unified tools collected: {len(all_tools)} tools from {len(active_servers)} servers")
        if failed_servers:
            logger.warning(f"⚠️ Failed servers: {failed_servers}")
        
        # Prepare response
        response_data = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": all_tools
            }
        }
        
        # Queue response
        await self.transport.message_queue.put(response_data)
        
        return JSONResponse(content={"status": "processing"}, status_code=202)
    
    async def handle_tool_call(self, message: Dict[str, Any]) -> JSONResponse:
        """
        Execute tool call on appropriate server
        """
        request_id = message.get("id")
        params = message.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        
        if not tool_name:
            error_response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32602,
                    "message": "Invalid params: 'name' is required"
                }
            }
            await self.transport.message_queue.put(error_response)
            return JSONResponse(content={"status": "processing"}, status_code=202)
        
        # Note: Orchestrator meta-tools removed per user request
        
        # Parse namespace and route to server
        try:
            # Check if tool name is namespaced
            if self.transport.tool_naming.is_namespaced(tool_name):
                # parse_tool_name returns a tuple (server_name, original_name)
                server_name, original_name = self.transport.tool_naming.parse_tool_name(tool_name)
            else:
                # For non-namespaced tools, try to find which server provides it
                # This is useful for single-server scenarios or when client doesn't use namespace
                server_name = await self._find_server_for_tool(tool_name)
                if not server_name:
                    # If no server found, raise error
                    raise ValueError(f"No server provides tool '{tool_name}'")
                original_name = tool_name
                logger.info(f"Auto-resolved tool '{tool_name}' to server '{server_name}'")
        except (ValueError, TypeError) as e:
            logger.error(f"Failed to parse tool name '{tool_name}': {e}")
            error_response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Tool not found: {tool_name}"
                }
            }
            await self.transport.message_queue.put(error_response)
            return JSONResponse(content={"status": "processing"}, status_code=202)
        
        if not server_name:
            error_response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Tool not found: {tool_name}"
                }
            }
            await self.transport.message_queue.put(error_response)
            return JSONResponse(content={"status": "processing"}, status_code=202)
        
        # Execute tool on target server
        try:
            result = await self._execute_tool_on_server(
                server_name, original_name, arguments
            )
            
            response_data = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result
            }
            
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            response_data = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32603,
                    "message": str(e)
                }
            }
        
        await self.transport.message_queue.put(response_data)
        return JSONResponse(content={"status": "processing"}, status_code=202)
    
    async def handle_resources_list(self, message: Dict[str, Any]) -> JSONResponse:
        """
        📚 Unified MCP resources/list 처리
        
        Roo 클라이언트 호환성을 위한 빈 리소스 목록 반환.
        현재 mcp-orch는 툴 중심으로 구현되어 있어 리소스는 지원하지 않음.
        """
        try:
            request_id = message.get("id")
            
            logger.info(f"📚 Processing unified resources/list for session {self.transport.session_id}, id={request_id}")
            
            # MCP 표준 리소스 응답 (빈 목록)
            response_data = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "resources": []
                }
            }
            
            # Unified SSE에서는 응답을 메시지 큐에 넣어야 함
            logger.info(f"📤 Queueing resources/list response for Unified SSE session {self.transport.session_id}")
            await self.transport.message_queue.put(response_data)
            
            logger.info(f"✅ Unified resources/list complete: 0 resources (tools-focused implementation)")
            
            # HTTP 202 Accepted 반환 (실제 응답은 SSE를 통해 전송됨)
            return JSONResponse(content={"status": "processing"}, status_code=202)
            
        except Exception as e:
            logger.error(f"❌ Unified resources/list error: {e}")
            
            # 에러 응답
            error_response_data = {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {
                    "code": -32000,
                    "message": f"Resources list failed: {str(e)}"
                }
            }
            
            # 에러 응답도 메시지 큐를 통해 전송
            await self.transport.message_queue.put(error_response_data)
            
            # HTTP 202 Accepted 반환 (실제 응답은 SSE를 통해 전송됨)
            return JSONResponse(content={"status": "processing"}, status_code=202)
    
    async def handle_resources_templates_list(self, message: Dict[str, Any]) -> JSONResponse:
        """
        📋 Unified MCP resources/templates/list 처리
        
        Roo 클라이언트 호환성을 위한 빈 리소스 템플릿 목록 반환.
        현재 mcp-orch는 툴 중심으로 구현되어 있어 리소스 템플릿은 지원하지 않음.
        """
        try:
            request_id = message.get("id")
            
            logger.info(f"📋 Processing unified resources/templates/list for session {self.transport.session_id}, id={request_id}")
            
            # MCP 표준 리소스 템플릿 응답 (빈 목록)
            response_data = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "resourceTemplates": []
                }
            }
            
            # Unified SSE에서는 응답을 메시지 큐에 넣어야 함
            logger.info(f"📤 Queueing resources/templates/list response for Unified SSE session {self.transport.session_id}")
            await self.transport.message_queue.put(response_data)
            
            logger.info(f"✅ Unified resources/templates/list complete: 0 templates (tools-focused implementation)")
            
            # HTTP 202 Accepted 반환 (실제 응답은 SSE를 통해 전송됨)
            return JSONResponse(content={"status": "processing"}, status_code=202)
            
        except Exception as e:
            logger.error(f"❌ Unified resources/templates/list error: {e}")
            
            # 에러 응답
            error_response_data = {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {
                    "code": -32000,
                    "message": f"Resource templates list failed: {str(e)}"
                }
            }
            
            # 에러 응답도 메시지 큐를 통해 전송
            await self.transport.message_queue.put(error_response_data)
            
            # HTTP 202 Accepted 반환 (실제 응답은 SSE를 통해 전송됨)
            return JSONResponse(content={"status": "processing"}, status_code=202)
    
    def _create_namespaced_tool(self, tool: Dict[str, Any], server) -> Dict[str, Any]:
        """Create namespaced version of tool (matches original logic)"""
        original_name = tool.get("name", "")
        
        # Get or create namespace for server (following original logic)
        namespace_name = self.transport.namespace_registry.get_original_name(server.name)
        if not namespace_name:
            namespace_name = self.transport.namespace_registry.register_server(server.name)
        
        # Use legacy mode setting from transport
        legacy_mode = getattr(self.transport, '_legacy_mode', False)
        
        processed_tool = tool.copy()
        
        # Fix schema field naming (matches original logic)
        if 'schema' in processed_tool and 'inputSchema' not in processed_tool:
            processed_tool['inputSchema'] = processed_tool.pop('schema')
        elif 'inputSchema' not in processed_tool:
            processed_tool['inputSchema'] = {
                "type": "object",
                "properties": {},
                "required": []
            }
        
        if legacy_mode:
            # Legacy mode: no namespace, original tool name
            pass  # Keep original tool name
        else:
            # Standard mode: apply namespace
            processed_tool['name'] = create_namespaced_name(namespace_name, original_name)
            
            # Add metadata
            processed_tool['_source_server'] = server.name
            processed_tool['_original_name'] = original_name
            processed_tool['_namespace'] = namespace_name
        
        return processed_tool
    
    
    async def _find_server_for_tool(self, tool_name: str) -> Optional[str]:
        """
        Find which server provides a specific tool.
        
        This is useful for single-server scenarios or when clients don't use namespaces.
        
        Args:
            tool_name: The non-namespaced tool name to search for
            
        Returns:
            Server name if found, None otherwise
        """
        active_servers = [s for s in self.transport.project_servers if s.is_enabled]
        
        logger.info(f"🔍 Searching for tool '{tool_name}' across {len(active_servers)} servers")
        
        for server in active_servers:
            try:
                # Check if server is available
                if not self.transport._is_server_available(server.name):
                    continue
                    
                # Build server config
                server_config = self.transport._build_server_config_for_server(server)
                if not server_config:
                    continue
                    
                # Get tools from server
                tools = await mcp_connection_service.get_server_tools(
                    str(server.id), server_config, project_id=str(self.transport.project_id)
                )
                
                if tools:
                    # Check if this server has the requested tool
                    for tool in tools:
                        if tool.get('name') == tool_name:
                            logger.info(f"✅ Found tool '{tool_name}' in server '{server.name}'")
                            return server.name
                            
            except Exception as e:
                logger.debug(f"Error checking server {server.name} for tool {tool_name}: {e}")
                continue
        
        logger.warning(f"❌ Tool '{tool_name}' not found in any server")
        return None
    
    async def _execute_tool_on_server(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Execute tool on specific server"""
        # Find server by name
        server = next((s for s in self.transport.project_servers if s.name == server_name), None)
        if not server:
            raise Exception(f"Server not found: {server_name}")
        
        # Build server config
        server_config = self.transport._build_server_config_for_server(server)
        if not server_config:
            raise Exception(f"Failed to build config for server: {server_name}")
        
        # Execute tool
        result = await mcp_connection_service.call_tool(
            str(server.id),
            server_config,
            tool_name,
            arguments,
            str(self.transport.project_id)
        )
        
        return result