"""Add SSE server support fields

Revision ID: sse_server_support  
Revises: add_process_tracking_fields
Create Date: 2025-01-18

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'sse_server_support'
down_revision = 'add_process_tracking_fields'  # 최신 revision ID로 설정
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add SSE server support fields to mcp_servers table."""
    
    # 1. command 필드를 nullable로 변경 (SSE 서버는 command가 필요없음)
    op.alter_column('mcp_servers', 'command',
               existing_type=sa.VARCHAR(length=500),
               nullable=True)
    
    # 2. SSE 서버 전용 필드들 추가
    op.add_column('mcp_servers', sa.Column('url', sa.String(length=2000), nullable=True, comment='SSE server URL (required for SSE transport)'))
    op.add_column('mcp_servers', sa.Column('_headers_encrypted', sa.Text(), nullable=True, comment='Encrypted JSON of HTTP headers for SSE requests'))
    op.add_column('mcp_servers', sa.Column('headers', sa.JSON(), nullable=True, comment='Legacy plaintext headers (deprecated)'))
    
    # 3. transport_type 필드 코멘트 업데이트
    op.alter_column('mcp_servers', 'transport_type',
               existing_type=sa.VARCHAR(length=50),
               comment='Transport type: "stdio" or "sse"')
    
    print("✅ Added SSE server support fields to mcp_servers table")
    print("   - command: now nullable (SSE servers don't need command)")
    print("   - url: SSE server endpoint URL")
    print("   - _headers_encrypted: encrypted HTTP headers")  
    print("   - headers: legacy plaintext headers (deprecated)")


def downgrade() -> None:
    """Remove SSE server support fields from mcp_servers table."""
    
    # SSE 서버가 존재하는지 확인
    connection = op.get_bind()
    result = connection.execute(
        sa.text("SELECT COUNT(*) FROM mcp_servers WHERE transport_type IN ('sse', 'http')")
    )
    sse_count = result.scalar()
    
    if sse_count > 0:
        raise Exception(f"Cannot downgrade: {sse_count} SSE servers exist. Please migrate them to stdio or delete them first.")
    
    # SSE 전용 필드들 제거
    op.drop_column('mcp_servers', 'headers')
    op.drop_column('mcp_servers', '_headers_encrypted')
    op.drop_column('mcp_servers', 'url')
    
    # command 필드를 다시 NOT NULL로 변경
    op.alter_column('mcp_servers', 'command',
               existing_type=sa.VARCHAR(length=500),
               nullable=False)
    
    print("✅ Removed SSE server support fields from mcp_servers table")