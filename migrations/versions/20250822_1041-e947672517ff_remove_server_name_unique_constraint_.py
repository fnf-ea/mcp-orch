"""remove_server_name_unique_constraint_allow_duplicates_per_project

Revision ID: e947672517ff
Revises: sse_server_support
Create Date: 2025-08-22 10:41:38.932481

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e947672517ff'
down_revision: Union[str, None] = 'sse_server_support'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add unique constraint on (project_id, name) combination to ensure
    server names are unique within a project but can be duplicated across projects.
    
    Note: No need to remove existing unique constraint on name field as it doesn't exist.
    """
    # Check if the index already exists to make migration idempotent
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT 1 FROM pg_indexes 
        WHERE indexname = 'ix_mcp_servers_project_name'
    """))
    
    if not result.fetchone():
        # Create unique index on (project_id, name) combination
        # This ensures server names are unique within a project but can be duplicated across projects
        op.create_index(
            'ix_mcp_servers_project_name',
            'mcp_servers',
            ['project_id', 'name'],
            unique=True,
            postgresql_where=sa.text('name IS NOT NULL')
        )
        
        # Log the migration
        print("✅ Added unique constraint on (project_id, name) combination")
        print("✅ Server names can now be duplicated across different projects")
    else:
        print("ℹ️ Index ix_mcp_servers_project_name already exists, skipping creation")


def downgrade() -> None:
    """
    Remove the (project_id, name) unique constraint.
    
    Note: This doesn't add back a unique constraint on name field since 
    it didn't exist before this migration.
    """
    # Check if the index exists before trying to drop it
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT 1 FROM pg_indexes 
        WHERE indexname = 'ix_mcp_servers_project_name'
    """))
    
    if result.fetchone():
        # Remove the project-name unique index
        op.drop_index('ix_mcp_servers_project_name', table_name='mcp_servers')
        print("✅ Removed unique constraint on (project_id, name) combination")
    else:
        print("ℹ️ Index ix_mcp_servers_project_name doesn't exist, skipping removal")
