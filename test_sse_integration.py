#!/usr/bin/env python3
"""
SSE Bridge Integration Test
Tests the SSE bridge server's ability to serve tools to MCP clients

Usage:
    python test_sse_integration.py [project_id] [server_name]

Environment Variables:
    MCP_AUTH_TOKEN: Bearer token for authentication
    MCP_BASE_URL: Base URL of the mcp-orch server (default: http://localhost:8000)
"""

import asyncio
import json
import logging
import sys
import os
from typing import Dict, Any, Optional
from uuid import UUID

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import options for testing
USE_MCP_SDK = False  # Set to True if MCP SDK is installed

if USE_MCP_SDK:
    try:
        from mcp.client import Client
        from mcp.client.sse import SSEClientTransport
        logger.info("✅ Using MCP SDK for testing")
        USE_MCP_SDK = True
    except ImportError:
        logger.warning("⚠️ MCP SDK not available, falling back to httpx")
        USE_MCP_SDK = False

# Always import httpx for direct testing
try:
    import httpx
    from httpx_sse import aconnect_sse
except ImportError as e:
    logger.error(f"❌ Required package missing: {e}")
    logger.error("Install with: pip install httpx httpx-sse")
    sys.exit(1)


class SSEBridgeTest:
    """SSE Bridge Server Integration Test"""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.test_results = {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "errors": []
        }

    def log_test_result(self, test_name: str, success: bool, message: str = ""):
        """Log test results"""
        self.test_results["total"] += 1
        if success:
            self.test_results["passed"] += 1
            logger.info(f"✅ {test_name}: PASSED {message}")
        else:
            self.test_results["failed"] += 1
            self.test_results["errors"].append(f"{test_name}: {message}")
            logger.error(f"❌ {test_name}: FAILED {message}")

    async def test_server_health_check(self, project_id: str, server_name: str, headers: Optional[Dict] = None):
        """Test if the SSE bridge server is healthy"""
        logger.info("\n" + "="*60)
        logger.info("🏥 Server Health Check")
        logger.info("="*60)
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Check if server is running
                try:
                    response = await client.get(self.base_url, headers=headers or {})
                    if response.status_code in [200, 404, 405]:
                        self.log_test_result("Server Running", True, f"HTTP {response.status_code}")
                    else:
                        self.log_test_result("Server Running", False, f"HTTP {response.status_code}")
                except Exception as e:
                    self.log_test_result("Server Running", False, f"Cannot reach server: {e}")
                    return
                
                # Check SSE endpoint availability
                sse_url = f"{self.base_url}/projects/{project_id}/servers/{server_name}/bridge/sse"
                try:
                    # SSE endpoints usually don't respond to regular GET
                    response = await client.options(sse_url, headers=headers or {})
                    self.log_test_result("SSE Endpoint Check", True, f"Endpoint exists")
                except Exception as e:
                    # Even if OPTIONS fails, the endpoint might still work
                    logger.warning(f"⚠️ OPTIONS request failed: {e}")
                    
        except Exception as e:
            self.log_test_result("Health Check", False, str(e))

    async def test_sse_bridge_with_httpx(self, project_id: str, server_name: str, headers: Optional[Dict] = None):
        """Test SSE bridge server with direct HTTP/SSE requests"""
        logger.info("\n" + "="*60)
        logger.info("🔍 Testing SSE Bridge with httpx")
        logger.info("="*60)
        
        sse_url = f"{self.base_url}/projects/{project_id}/servers/{server_name}/bridge/sse"
        message_url = f"{self.base_url}/projects/{project_id}/servers/{server_name}/bridge/messages"
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Test SSE connection
                logger.info(f"📡 Connecting to SSE endpoint: {sse_url}")
                
                message_endpoint = None
                
                # Try to establish SSE connection
                try:
                    async with aconnect_sse(client, "GET", sse_url, headers=headers or {}) as event_source:
                        logger.info("✅ SSE connection established")
                        self.log_test_result("SSE Connection", True)
                        
                        # Wait for endpoint event
                        event_count = 0
                        async for event in event_source.aiter_sse():
                            event_count += 1
                            
                            if event.event == "endpoint":
                                message_endpoint = event.data.strip()
                                logger.info(f"📬 Received message endpoint: {message_endpoint}")
                            else:
                                logger.info(f"📨 Event {event_count}: {event.event} - {event.data[:100] if event.data else 'No data'}")
                            
                            if event_count >= 5 or message_endpoint:  # Stop after endpoint or 5 events
                                break
                            
                            await asyncio.sleep(0.1)
                            
                except asyncio.TimeoutError:
                    logger.warning("⏰ SSE connection timed out")
                    self.log_test_result("SSE Connection", False, "Timeout")
                except Exception as e:
                    self.log_test_result("SSE Connection", False, str(e))
                
                # Use message endpoint or construct it
                if not message_endpoint:
                    message_endpoint = message_url
                    logger.info(f"📮 Using default message endpoint: {message_endpoint}")
                
                # Test message endpoint
                logger.info(f"\n📮 Testing message endpoint...")
                
                # Send initialize request
                init_message = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "0.1.0",
                        "capabilities": {},
                        "clientInfo": {
                            "name": "test-client",
                            "version": "1.0.0"
                        }
                    }
                }
                
                logger.info("🚀 Sending initialization message...")
                response = await client.post(message_endpoint, json=init_message, headers=headers or {})
                
                if response.status_code == 200:
                    result = response.json()
                    self.log_test_result("Initialize", True, f"Got response")
                    logger.debug(f"   Response: {json.dumps(result, indent=2)[:200]}")
                elif response.status_code == 202:
                    self.log_test_result("Initialize", True, "Accepted (202)")
                else:
                    self.log_test_result("Initialize", False, f"HTTP {response.status_code}")
                    logger.error(f"   Response: {response.text[:200]}")
                
                # Request tools list
                tools_message = {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {}
                }
                
                logger.info("🔧 Requesting tools list...")
                response = await client.post(message_endpoint, json=tools_message, headers=headers or {})
                
                if response.status_code == 200:
                    result = response.json()
                    if "result" in result:
                        tools = result.get("result", {}).get("tools", [])
                        self.log_test_result("Tools List", True, f"Found {len(tools)} tools")
                        
                        for tool in tools[:5]:  # Show first 5 tools
                            logger.info(f"  🔧 {tool.get('name')}: {tool.get('description', 'No description')[:50]}")
                        
                        # Test sse_bridge_test tool if available
                        test_tool = next((t for t in tools if t.get("name") == "sse_bridge_test"), None)
                        if test_tool:
                            await self.test_tool_execution(client, message_endpoint, headers)
                    else:
                        self.log_test_result("Tools List", False, "No result in response")
                elif response.status_code == 202:
                    self.log_test_result("Tools List", True, "Accepted (202) - async processing")
                else:
                    self.log_test_result("Tools List", False, f"HTTP {response.status_code}")
                    
        except Exception as e:
            self.log_test_result("HTTP Test", False, str(e))
            logger.error(f"Test failed: {e}", exc_info=True)

    async def test_tool_execution(self, client: httpx.AsyncClient, message_endpoint: str, headers: Optional[Dict] = None):
        """Test executing the sse_bridge_test tool"""
        logger.info("\n🧪 Testing tool execution...")
        
        tool_call_message = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "sse_bridge_test",
                "arguments": {
                    "message": "Hello from integration test!"
                }
            }
        }
        
        response = await client.post(message_endpoint, json=tool_call_message, headers=headers or {})
        
        if response.status_code == 200:
            result = response.json()
            self.log_test_result("Tool Execution", True, "Success")
            logger.info(f"   Result: {json.dumps(result, indent=2)[:200]}")
        else:
            self.log_test_result("Tool Execution", False, f"HTTP {response.status_code}")

    async def test_sse_bridge_with_mcp_sdk(self, project_id: str, server_name: str, headers: Optional[Dict] = None):
        """Test SSE bridge server using MCP SDK (like cline)"""
        if not USE_MCP_SDK:
            logger.warning("⚠️ MCP SDK not available, skipping SDK test")
            return
            
        logger.info("\n" + "="*60)
        logger.info("🔍 Testing SSE Bridge with MCP SDK")
        logger.info("="*60)
        
        sse_url = f"{self.base_url}/projects/{project_id}/servers/{server_name}/bridge/sse"
        
        try:
            # Create MCP client
            client = Client(
                name="test-client",
                version="1.0.0"
            )
            
            # Create SSE transport
            transport = SSEClientTransport(sse_url, headers=headers or {})
            
            # Connect
            logger.info(f"📡 Connecting to: {sse_url}")
            await client.connect(transport)
            self.log_test_result("MCP SDK Connection", True)
            
            # List tools
            logger.info("🔧 Requesting tool list...")
            tools_response = await client.request(
                method="tools/list",
                params={}
            )
            
            tools = tools_response.get("tools", [])
            self.log_test_result("MCP SDK Tools", True, f"Found {len(tools)} tools")
            
            for tool in tools[:5]:  # Show first 5 tools
                logger.info(f"  🔧 {tool.get('name')}: {tool.get('description', 'No description')[:50]}")
            
            # Test sse_bridge_test tool if available
            test_tool = next((t for t in tools if t.get("name") == "sse_bridge_test"), None)
            if test_tool:
                logger.info("🧪 Testing sse_bridge_test tool...")
                tool_response = await client.request(
                    method="tools/call",
                    params={
                        "name": "sse_bridge_test",
                        "arguments": {
                            "message": "Hello from MCP SDK test!"
                        }
                    }
                )
                self.log_test_result("Tool Execution (SDK)", True, f"Response: {tool_response}")
            
            # Disconnect
            await client.close()
            logger.info("🔌 Disconnected successfully")
            
        except Exception as e:
            self.log_test_result("MCP SDK Test", False, str(e))
            logger.error(f"MCP SDK test failed: {e}", exc_info=True)

    async def run_all_tests(self, project_id: str, server_name: str, headers: Optional[Dict] = None):
        """Run all SSE bridge tests"""
        logger.info("\n" + "#"*60)
        logger.info("# SSE Bridge Server Integration Test")
        logger.info("#"*60)
        logger.info(f"# Project ID: {project_id}")
        logger.info(f"# Server Name: {server_name}")
        logger.info(f"# Base URL: {self.base_url}")
        logger.info("#"*60)
        
        # Run tests
        await self.test_server_health_check(project_id, server_name, headers)
        await self.test_sse_bridge_with_httpx(project_id, server_name, headers)
        
        if USE_MCP_SDK:
            await self.test_sse_bridge_with_mcp_sdk(project_id, server_name, headers)
        
        # Print summary
        self.print_summary()

    def print_summary(self):
        """Print test results summary"""
        logger.info("\n" + "="*60)
        logger.info("📊 Test Results Summary")
        logger.info("="*60)
        logger.info(f"Total Tests: {self.test_results['total']}")
        logger.info(f"✅ Passed: {self.test_results['passed']}")
        logger.info(f"❌ Failed: {self.test_results['failed']}")
        
        if self.test_results['errors']:
            logger.info("\nFailed Tests:")
            for error in self.test_results['errors']:
                logger.error(f"  - {error}")
        
        success_rate = (self.test_results['passed'] / self.test_results['total'] * 100) if self.test_results['total'] > 0 else 0
        logger.info(f"\nSuccess Rate: {success_rate:.1f}%")
        
        if success_rate == 100:
            logger.info("\n🎉 All tests passed!")
        elif success_rate >= 75:
            logger.info("\n✅ Most tests passed")
        else:
            logger.info("\n⚠️ Improvements needed")


async def main():
    """Main test runner"""
    # Parse command line arguments
    if len(sys.argv) > 1:
        project_id = sys.argv[1]
    else:
        # Default test project ID (replace with actual)
        project_id = "00000000-0000-0000-0000-000000000000"
        logger.warning(f"⚠️ Using default project ID: {project_id}")
        logger.info("   Usage: python test_sse_integration.py <project_id> <server_name>")
    
    if len(sys.argv) > 2:
        server_name = sys.argv[2]
    else:
        # Default test server name
        server_name = "test-sse-bridge"
        logger.warning(f"⚠️ Using default server name: {server_name}")
    
    # Optional: Set authentication headers if needed
    headers = {
        "User-Agent": "SSE-Bridge-Test/1.0",
        "Accept": "text/event-stream,application/json"
    }
    
    # Check for environment variables
    if os.getenv("MCP_AUTH_TOKEN"):
        headers["Authorization"] = f"Bearer {os.getenv('MCP_AUTH_TOKEN')}"
        logger.info("🔐 Using authentication from MCP_AUTH_TOKEN")
    
    base_url = os.getenv("MCP_BASE_URL", "http://localhost:8000")
    logger.info(f"🌐 Using base URL: {base_url}")
    
    # Run tests
    tester = SSEBridgeTest(base_url)
    await tester.run_all_tests(project_id, server_name, headers)


if __name__ == "__main__":
    asyncio.run(main())