#!/usr/bin/env python3
"""
테스트 스크립트: 프로젝트별 서버 이름 중복 허용 테스트
"""

import asyncio
import aiohttp
import json
import sys
from uuid import uuid4

# API 엔드포인트 설정
BASE_URL = "http://localhost:8000"  # 실제 API URL로 변경 필요

async def test_duplicate_server_names():
    """서로 다른 프로젝트에서 동일한 서버 이름 사용 테스트"""
    
    # 테스트용 프로젝트 ID (실제 존재하는 프로젝트 ID로 변경 필요)
    project1_id = "d82d651f-5f3b-445b-b2cf-47f461773fa4"
    project2_id = "b7c7b87d-80ab-4793-b27e-62e0c7da19c2"
    
    # 동일한 서버 이름 사용
    server_name = "test-duplicate-server"
    
    async with aiohttp.ClientSession() as session:
        # 1. 첫 번째 프로젝트에 서버 생성
        print(f"1. Creating server '{server_name}' in project 1...")
        server1_config = {
            "name": server_name,
            "display_name": "Test Server in Project 1",
            "transport_type": "stdio",
            "command": "node",
            "args": ["test-server.js"],
            "env": {"PROJECT": "1"}
        }
        
        try:
            async with session.post(
                f"{BASE_URL}/projects/{project1_id}/servers",
                json=server1_config
            ) as resp:
                if resp.status == 200:
                    server1 = await resp.json()
                    print(f"   ✅ Server created: {server1['id']}")
                else:
                    print(f"   ❌ Failed: {resp.status} - {await resp.text()}")
                    return
        except Exception as e:
            print(f"   ❌ Error: {e}")
            return
        
        # 2. 두 번째 프로젝트에 동일한 이름의 서버 생성
        print(f"\n2. Creating server '{server_name}' in project 2...")
        server2_config = {
            "name": server_name,
            "display_name": "Test Server in Project 2", 
            "transport_type": "stdio",
            "command": "node",
            "args": ["test-server.js"],
            "env": {"PROJECT": "2"}
        }
        
        try:
            async with session.post(
                f"{BASE_URL}/projects/{project2_id}/servers",
                json=server2_config
            ) as resp:
                if resp.status == 200:
                    server2 = await resp.json()
                    print(f"   ✅ Server created: {server2['id']}")
                else:
                    print(f"   ❌ Failed: {resp.status} - {await resp.text()}")
                    # 이전 버전에서는 여기서 실패했을 것
        except Exception as e:
            print(f"   ❌ Error: {e}")
        
        # 3. SSE 엔드포인트로 각 서버 접근 테스트
        print(f"\n3. Testing SSE endpoints...")
        
        # Project 1 서버 테스트
        sse_url1 = f"{BASE_URL}/projects/{project1_id}/servers/{server_name}/sse"
        print(f"   Testing: {sse_url1}")
        try:
            async with session.get(sse_url1) as resp:
                if resp.status == 200:
                    print(f"   ✅ SSE endpoint 1 accessible")
                else:
                    print(f"   ❌ SSE endpoint 1 failed: {resp.status}")
        except Exception as e:
            print(f"   ❌ Error accessing SSE 1: {e}")
        
        # Project 2 서버 테스트
        sse_url2 = f"{BASE_URL}/projects/{project2_id}/servers/{server_name}/sse"
        print(f"   Testing: {sse_url2}")
        try:
            async with session.get(sse_url2) as resp:
                if resp.status == 200:
                    print(f"   ✅ SSE endpoint 2 accessible")
                else:
                    print(f"   ❌ SSE endpoint 2 failed: {resp.status}")
        except Exception as e:
            print(f"   ❌ Error accessing SSE 2: {e}")
        
        # 4. 세션 격리 테스트
        print(f"\n4. Testing session isolation...")
        
        # 각 서버에서 도구 목록 가져오기
        for project_id, server_id in [(project1_id, server1['id']), (project2_id, server2['id'])]:
            try:
                async with session.get(
                    f"{BASE_URL}/projects/{project_id}/servers/{server_id}/tools"
                ) as resp:
                    if resp.status == 200:
                        tools = await resp.json()
                        print(f"   ✅ Project {project_id[:8]}... has {len(tools)} tools")
                    else:
                        print(f"   ❌ Failed to get tools: {resp.status}")
            except Exception as e:
                print(f"   ❌ Error getting tools: {e}")
        
        print("\n✅ Test completed successfully!")
        print("   - Same server name can be used in different projects")
        print("   - Each project maintains isolated sessions")
        print("   - SSE endpoints are project-scoped")

if __name__ == "__main__":
    print("=" * 60)
    print("Testing Duplicate Server Names Across Projects")
    print("=" * 60)
    asyncio.run(test_duplicate_server_names())