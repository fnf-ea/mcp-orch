# 프로젝트 범위 서버 관리 (Project-Scoped Server Management)

## 개요
MCP-Orch가 이제 프로젝트별로 독립적인 서버 이름 공간을 지원합니다. 이를 통해 서로 다른 프로젝트에서 동일한 서버 이름을 사용할 수 있으며, 각 프로젝트의 서버는 완전히 격리된 세션을 유지합니다.

## 주요 변경사항

### 1. 데이터베이스 스키마
- **이전**: `mcp_servers.name` 필드에 UNIQUE 제약 조건이 있어 전체 시스템에서 서버 이름이 고유해야 했음
- **현재**: `(project_id, name)` 조합으로만 고유성 보장, 프로젝트별로 동일한 이름 허용

### 2. 세션 관리
- **세션 키 형식**: `{project_id}.{server_id}` 또는 `{project_id}.{server_name}`
- 각 프로젝트의 서버는 독립적인 세션을 유지
- SSE와 stdio 모두에서 프로젝트별 격리 보장

### 3. API 엔드포인트
모든 서버 관련 엔드포인트는 프로젝트 컨텍스트 내에서 작동:
```
/projects/{project_id}/servers/{server_name}
/projects/{project_id}/servers/{server_name}/sse
/projects/{project_id}/servers/{server_name}/tools
```

### 4. SSE Bridge 개선
- 프로젝트별 서버 식별: `f"{project_id}.{server_record.id}"` 형식 사용
- 도구 호출 시 프로젝트 컨텍스트 유지
- 세션 충돌 방지를 위한 복합 키 사용

## 구현 세부사항

### 세션 식별자 해석 (`_resolve_server_id`)
```python
def _resolve_server_id(server_id: str) -> Tuple[Optional[UUID], Optional[UUID]]:
    """
    server_id 형식:
    - "project_id.server_name": 프로젝트와 서버 이름으로 조회
    - "uuid": 직접 서버 UUID
    - "uuid_server_name": UUID 부분 추출
    """
```

### 세션 생성 및 관리
```python
# 프로젝트 컨텍스트를 포함한 세션 키
session_key = f"{project_uuid}.{server_id}" if project_uuid else server_id

# 세션 저장 및 조회
self.sessions[session_key] = session
```

## 마이그레이션 가이드

### 1. 데이터베이스 마이그레이션
```sql
-- unique constraint 제거
ALTER TABLE mcp_servers DROP CONSTRAINT IF EXISTS mcp_servers_name_key;

-- 프로젝트 + 이름 조합 인덱스 추가
CREATE UNIQUE INDEX ix_mcp_servers_project_name 
ON mcp_servers(project_id, name) 
WHERE name IS NOT NULL;
```

### 2. 기존 세션 정리
마이그레이션 후 기존 세션을 재시작하여 새로운 키 형식 적용:
```bash
# 모든 서버 재시작
systemctl restart mcp-orch
```

## 장점

1. **프로젝트 격리**: 각 프로젝트가 독립적인 서버 네임스페이스 보유
2. **이름 충돌 방지**: 프로젝트 간 서버 이름 충돌 없음
3. **세션 격리**: 프로젝트별 독립적인 세션 관리
4. **확장성**: 멀티테넌트 환경에 적합
5. **유연성**: 각 프로젝트가 자체 명명 규칙 사용 가능

## 호환성

### 이전 버전과의 호환성
- UUID 기반 서버 ID는 계속 작동
- 기존 API 엔드포인트 구조 유지
- 점진적 마이그레이션 가능

### 영향받는 컴포넌트
- `McpSessionManager`: 세션 키 형식 변경
- `McpOrchestrator`: 프로젝트 컨텍스트 전달
- `mcp_sdk_sse_bridge.py`: 프로젝트별 서버 식별
- 데이터베이스 스키마: unique constraint 변경

## 테스트

### 중복 이름 테스트
```python
# test_duplicate_server_names.py 실행
python test_duplicate_server_names.py
```

### 확인 항목
- [ ] 서로 다른 프로젝트에서 동일한 서버 이름 생성 가능
- [ ] 각 프로젝트의 SSE 엔드포인트 독립적으로 작동
- [ ] 도구 호출 시 프로젝트별 세션 사용
- [ ] 세션 타임아웃 및 정리가 프로젝트별로 작동

## 주의사항

1. **서버 이름 조회**: 항상 프로젝트 컨텍스트 내에서 조회
2. **세션 관리**: 세션 키에 프로젝트 ID 포함 필수
3. **로깅**: 디버깅을 위해 프로젝트 ID 포함하여 로그 기록
4. **권한 확인**: 프로젝트 접근 권한 확인 필수

## 롤백 계획

필요시 이전 버전으로 롤백:
```sql
-- unique constraint 복원
ALTER TABLE mcp_servers ADD CONSTRAINT mcp_servers_name_key UNIQUE (name);

-- 프로젝트별 인덱스 제거
DROP INDEX IF EXISTS ix_mcp_servers_project_name;
```

## 향후 개선사항

1. **네임스페이스 지원**: 프로젝트 내 추가 네임스페이스 계층
2. **서버 템플릿**: 프로젝트별 서버 템플릿 관리
3. **일괄 작업**: 프로젝트 내 모든 서버 일괄 관리 API
4. **모니터링**: 프로젝트별 서버 상태 대시보드