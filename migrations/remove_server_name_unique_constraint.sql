-- 서버 이름 중복 허용을 위한 마이그레이션
-- 프로젝트별로 서버 이름이 중복될 수 있도록 기존 unique constraint 제거

-- 1. 기존 unique constraint 확인 및 제거
-- PostgreSQL의 경우
DO $$ 
BEGIN
    -- mcp_servers 테이블의 unique constraint 제거
    IF EXISTS (
        SELECT 1 
        FROM information_schema.table_constraints 
        WHERE table_name = 'mcp_servers' 
        AND constraint_type = 'UNIQUE'
        AND constraint_name LIKE '%name%'
    ) THEN
        ALTER TABLE mcp_servers DROP CONSTRAINT IF EXISTS mcp_servers_name_key;
        ALTER TABLE mcp_servers DROP CONSTRAINT IF EXISTS uix_mcp_servers_name;
        ALTER TABLE mcp_servers DROP CONSTRAINT IF EXISTS uk_mcp_servers_name;
    END IF;
    
    -- 프로젝트 + 이름 조합 unique constraint가 있다면 유지
    -- (project_id, name) 조합은 유지해야 함
    IF NOT EXISTS (
        SELECT 1 
        FROM pg_indexes 
        WHERE tablename = 'mcp_servers' 
        AND indexname = 'ix_mcp_servers_project_name'
    ) THEN
        CREATE UNIQUE INDEX ix_mcp_servers_project_name 
        ON mcp_servers(project_id, name) 
        WHERE name IS NOT NULL;
    END IF;
END $$;

-- 2. 변경 사항 확인
SELECT 
    tc.constraint_name,
    tc.constraint_type,
    kcu.column_name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu 
    ON tc.constraint_name = kcu.constraint_name
WHERE tc.table_name = 'mcp_servers'
    AND tc.constraint_type IN ('UNIQUE', 'PRIMARY KEY')
ORDER BY tc.constraint_type, tc.constraint_name;

-- 3. 인덱스 확인
SELECT 
    indexname,
    indexdef
FROM pg_indexes
WHERE tablename = 'mcp_servers'
ORDER BY indexname;

-- 롤백 스크립트 (필요한 경우)
-- ALTER TABLE mcp_servers ADD CONSTRAINT mcp_servers_name_key UNIQUE (name);