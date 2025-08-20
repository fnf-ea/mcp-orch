# SSE MCP 서버 통합 가이드

## 개요

mcp-orch는 이제 기존 stdio 방식 MCP 서버에 더해 **SSE(Server-Sent Events) 방식의 MCP 서버**를 완전히 지원합니다. 

### 지원되는 연결 방식

1. **stdio**: 프로세스 기반 로컬 MCP 서버 (기존 방식)
2. **SSE**: HTTP URL 기반 원격 MCP 서버 (신규)

## SSE MCP 서버란?

SSE MCP 서버는 HTTP 엔드포인트를 통해 연결되는 원격 MCP 서버입니다:

- **연결**: HTTP URL을 통한 SSE 스트림
- **통신**: JSON-RPC 2.0 over HTTP POST
- **장점**: 
  - 네트워크를 통한 원격 접근 가능
  - 방화벽 친화적 (HTTP/HTTPS)
  - 확장성 및 로드 밸런싱 지원
  - 클라우드 배포 용이

## 설정 방법

### 1. JSON 설정 파일에서 SSE 서버 정의

```json
{
  "mcpServers": {
    "remote-sse-server": {
      "type": "sse",
      "url": "http://10.150.0.36:8000/mcp",
      "timeout": 30,
      "headers": {
        "Authorization": "Bearer your-api-token",
        "X-Custom-Header": "value"
      },
      "autoApprove": ["safe_tool1", "safe_tool2"],
      "disabled": false
    }
  }
}
```

### 2. 프로젝트 API를 통한 SSE 서버 추가

```bash
curl -X POST "/projects/{project_id}/servers" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-jwt-token" \
  -d '{
    "name": "remote-analytics",
    "type": "sse",
    "url": "https://analytics-mcp.example.com/sse",
    "timeout": 60,
    "headers": {
      "X-API-Key": "your-remote-api-key",
      "X-Client-ID": "mcp-orch"
    },
    "auto_approve": ["get_metrics", "list_reports"]
  }'
```

### 3. 웹 UI를 통한 SSE 서버 관리

웹 관리 인터페이스에서 "Add Server" 버튼 클릭 후:
- **Server Type**: SSE 선택
- **URL**: 원격 MCP 서버 엔드포인트
- **Headers**: 인증 및 커스텀 헤더 설정
- **Timeout**: 연결 타임아웃 (초)

## 통합 아키텍처

mcp-orch의 새로운 통합 연결 관리자는 stdio와 SSE 서버를 동시에 관리합니다:

```
mcp-orch
├── stdio 서버들
│   ├── github-server (프로세스)
│   ├── notion-server (프로세스)
│   └── local-tools (프로세스)
├── SSE 서버들
│   ├── remote-analytics (HTTP)
│   ├── cloud-database (HTTPS)
│   └── external-api (HTTP)
└── 통합 엔드포인트
    └── /projects/{id}/unified/sse
```

## 설정 생성 API

mcp-orch는 Claude Desktop, Cursor 등을 위한 설정을 자동 생성합니다:

### 통합 모드 설정 생성

```bash
GET /projects/{project_id}/cline-config?unified=true
```

**결과**: 모든 서버(stdio + SSE)를 하나의 SSE 엔드포인트로 접근

```json
{
  "mcpServers": {
    "mcp-orch-unified-abc123": {
      "type": "sse", 
      "url": "https://your-server.com/projects/abc123/unified/sse",
      "timeout": 60,
      "headers": {
        "Authorization": "Bearer api_key_..."
      }
    }
  }
}
```

### 개별 서버 설정 생성

```bash
GET /projects/{project_id}/cline-config?unified=false
```

**결과**: 각 서버별 개별 설정 (stdio + SSE 혼재)

```json
{
  "mcpServers": {
    "project-abc123-github": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_TOKEN": "..."}
    },
    "project-abc123-analytics": {
      "type": "sse",
      "url": "https://analytics-mcp.example.com/sse",
      "headers": {"X-API-Key": "..."}
    }
  }
}
```

## 보안 고려사항

### 1. 인증 헤더 암호화

SSE 서버의 인증 헤더는 데이터베이스에 암호화되어 저장됩니다:

```python
# 헤더 설정 시 자동 암호화
server.headers = {
    "Authorization": "Bearer secret-token",
    "X-API-Key": "sensitive-key"  
}

# 조회 시 자동 복호화
headers = server.headers  # 복호화된 딕셔너리 반환
```

### 2. 프로젝트별 접근 제어

각 SSE 서버는 프로젝트 멤버십에 따른 접근 제어:

- **Owner**: 서버 추가/삭제/수정
- **Developer**: 서버 사용 및 설정 조회
- **Reporter**: 읽기 전용 접근

### 3. API 키 기반 인증

프로젝트별 API 키를 통한 SSE 서버 접근 제어

## 모니터링 및 상태 관리

### 서버 상태 확인

```bash
GET /projects/{project_id}/servers
```

**SSE 서버 응답 예제**:
```json
{
  "id": "server-uuid",
  "name": "remote-analytics", 
  "transport_type": "sse",
  "url": "https://analytics-mcp.example.com/sse",
  "timeout": 60,
  "headers_count": 2,
  "has_custom_headers": true,
  "disabled": false,
  "status": "active"
}
```

### 헬스 체크

통합 연결 관리자가 stdio와 SSE 서버 모두 모니터링:

- **stdio**: 프로세스 상태 및 stdout/stderr 모니터링
- **SSE**: HTTP 연결 상태 및 응답 시간 모니터링

## 개발자 가이드

### SSE MCP 서버 구현

mcp-orch와 호환되는 SSE MCP 서버 구현:

```python
from mcp.server.sse import SseServerTransport

# SSE 전송 계층 생성
sse = SseServerTransport("/messages/")

# MCP 서버 정의
@mcp_server.list_tools()
async def list_tools():
    return [
        {
            "name": "analyze_data",
            "description": "Analyze data from remote source",
            "inputSchema": {...}
        }
    ]

# Starlette 라우트 설정  
routes = [
    Route("/sse", endpoint=handle_sse, methods=["GET"]),
    Mount("/messages/", app=sse.handle_post_message),
]
```

### 커스텀 SSE 서버 테스트

```bash
# SSE 연결 테스트
curl -N -H "Accept: text/event-stream" \
  "http://10.150.0.36:8000/mcp"

# 메시지 전송 테스트  
curl -X POST "http://10.150.0.36:8000/messages/?session_id=abc123" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "tools/list", "id": 1}'
```

## 트러블슈팅

### 일반적인 문제

1. **연결 실패**
   ```
   Failed to connect to SSE server: Connection timeout
   ```
   - URL 접근 가능성 확인
   - 방화벽/네트워크 설정 검토
   - 서버 상태 확인

2. **인증 실패**
   ```
   HTTP 401: Unauthorized
   ```
   - 헤더의 인증 정보 확인
   - API 키 유효성 검증
   - 서버측 인증 설정 확인

3. **메시지 처리 오류**
   ```
   Invalid JSON-RPC 2.0 response
   ```
   - 서버의 JSON-RPC 2.0 준수 여부 확인
   - 응답 형식 검증

### 디버깅 로그

SSE 서버 연결 디버깅:

```bash
# 로그 레벨 DEBUG로 설정
export MCP_LOG_LEVEL=DEBUG

# mcp-orch 실행 후 로그 확인
tail -f logs/mcp-orch.log | grep SSE
```

## 마이그레이션 가이드

### 기존 설정 마이그레이션

stdio에서 SSE로 서버 마이그레이션:

1. **기존 stdio 서버 비활성화**
2. **SSE 서버로 재구성**
3. **설정 파일 업데이트**
4. **연결 테스트**

### 점진적 마이그레이션

stdio와 SSE 서버를 혼재하여 점진적 마이그레이션 가능:

```json
{
  "mcpServers": {
    "legacy-stdio": {"type": "stdio", "command": "..."},
    "new-sse": {"type": "sse", "url": "..."},
    "migrating": {"type": "stdio", "disabled": true}
  }
}
```

## 성능 최적화

### 연결 풀링

SSE 서버 연결은 HTTP 클라이언트 풀링으로 최적화:

- **재사용**: Keep-alive 연결
- **타임아웃**: 설정 가능한 연결/읽기 타임아웃
- **재시도**: 연결 실패 시 자동 재시도

### 모니터링 메트릭

- 연결 수립 시간
- 응답 시간
- 에러율
- 처리량 (RPS)

## 로드맵

### 계획된 기능

1. **자동 발견**: SSE 서버 자동 발견 및 등록
2. **로드 밸런싱**: 다중 SSE 엔드포인트 로드 밸런싱  
3. **캐싱**: 응답 캐싱 및 성능 최적화
4. **웹소켓**: WebSocket 전송 프로토콜 지원

### 호환성 로드맵

- **MCP v2.0**: 차세대 MCP 프로토콜 지원
- **gRPC**: 고성능 gRPC 전송 지원
- **GraphQL**: GraphQL over SSE 지원

---

## 요약

mcp-orch의 SSE 서버 지원으로 다음이 가능해졌습니다:

✅ **하이브리드 아키텍처**: stdio + SSE 서버 동시 운영
✅ **원격 접근**: 네트워크를 통한 MCP 서버 접근
✅ **보안**: 암호화된 헤더 및 프로젝트별 접근 제어
✅ **확장성**: 클라우드 배포 및 스케일링 지원
✅ **호환성**: 기존 설정과 완벽한 호환
✅ **모니터링**: 통합 상태 관리 및 헬스 체크

SSE MCP 서버 지원으로 mcp-orch는 더욱 유연하고 확장 가능한 MCP 오케스트레이션 플랫폼으로 발전했습니다.