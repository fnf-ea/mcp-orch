"""
설정 관리 모듈

애플리케이션 설정을 관리하고 환경 변수를 처리합니다.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerConfig(BaseModel):
    """서버 설정"""
    host: str = "0.0.0.0"
    port: int = 3000
    mode: str = "proxy"  # proxy 또는 batch
    workers: int = 1
    reload: bool = False
    log_level: str = "INFO"


class DatabaseConfig(BaseModel):
    """데이터베이스 설정"""
    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    password: str = "1234"
    name: str = "mcp_orch"
    url: Optional[str] = None
    sql_echo: bool = False


class LoggingConfig(BaseModel):
    """로깅 설정"""
    level: str = "INFO"
    format: str = "text"  # "text" or "json"
    output: str = "console"  # "console", "file", "both"
    file_path: Optional[str] = None
    
    @field_validator('level')
    @classmethod
    def validate_level(cls, v: str) -> str:
        """로그 레벨 유효성 검사"""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid_levels}")
        return v.upper()
    
    @field_validator('format')
    @classmethod
    def validate_format(cls, v: str) -> str:
        """로그 포맷 유효성 검사"""
        valid_formats = ["text", "json"]
        if v.lower() not in valid_formats:
            raise ValueError(f"Invalid log format: {v}. Must be one of {valid_formats}")
        return v.lower()
    
    @field_validator('output')
    @classmethod
    def validate_output(cls, v: str) -> str:
        """로그 출력 방식 유효성 검사"""
        valid_outputs = ["console", "file", "both"]
        if v.lower() not in valid_outputs:
            raise ValueError(f"Invalid log output: {v}. Must be one of {valid_outputs}")
        return v.lower()


class SecurityConfig(BaseModel):
    """보안 설정"""
    api_keys: List[Dict[str, Any]] = Field(default_factory=list)
    cors_origins: List[str] = Field(default_factory=lambda: ["*"])
    
    # 초기 관리자 계정 설정 (INITIAL_ADMIN_EMAIL 기반)
    initial_admin_email: Optional[str] = None
    
    # 사용자 자동 프로비저닝 설정
    auto_provision: bool = False


class LLMProviderConfig(BaseModel):
    """LLM 제공자별 설정"""
    endpoint: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 4096


class LLMConfig(BaseModel):
    """LLM 설정"""
    provider: str = "azure"  # azure, bedrock, openai, anthropic
    azure: Optional[LLMProviderConfig] = None
    bedrock: Optional[LLMProviderConfig] = None
    openai: Optional[LLMProviderConfig] = None
    anthropic: Optional[LLMProviderConfig] = None
    
    def get_active_provider(self) -> Optional[LLMProviderConfig]:
        """활성 프로바이더 설정 반환"""
        return getattr(self, self.provider, None)


class ExecutionConfig(BaseModel):
    """실행 엔진 설정"""
    max_parallel_tasks: int = 10
    task_timeout: int = 300  # seconds
    retry_count: int = 3
    retry_delay: int = 5  # seconds
    queue_size: int = 100


class MCPServerConfig(BaseModel):
    """MCP 서버 설정"""
    command: str
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    transport_type: str = "stdio"
    timeout: int = 60
    auto_approve: List[str] = Field(default_factory=list)
    disabled: bool = False


class MCPSessionConfig(BaseModel):
    """
    MCP Session Manager Configuration
    
    Controls the behavior of persistent MCP server sessions including:
    - How long to keep unused sessions alive
    - How frequently to check for expired sessions
    """
    
    # Session timeout: How long to keep unused sessions alive (in minutes)
    # Environment variable: MCP_SESSION_TIMEOUT_MINUTES
    # Default: 30 minutes
    session_timeout_minutes: int = Field(
        default=30,
        description="Session timeout in minutes - sessions unused for this duration will be terminated"
    )
    
    # Cleanup interval: How often to check for expired sessions (in minutes)
    # Environment variable: MCP_SESSION_CLEANUP_INTERVAL_MINUTES  
    # Default: 5 minutes
    cleanup_interval_minutes: int = Field(
        default=5,
        description="Cleanup interval in minutes - how often to check for expired sessions"
    )


class Settings(BaseSettings):
    """
    애플리케이션 설정
    
    환경 변수와 설정 파일에서 설정을 로드합니다.
    """
    
    # 서버 설정
    server: ServerConfig = Field(default_factory=ServerConfig)
    
    # 데이터베이스 설정
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    
    # 로깅 설정
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    
    # 보안 설정
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    
    # LLM 설정
    llm: LLMConfig = Field(default_factory=LLMConfig)
    
    # 실행 설정
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    
    # MCP 서버 설정
    mcp_servers: Dict[str, MCPServerConfig] = Field(default_factory=dict)
    
    # MCP 세션 매니저 설정
    mcp_session: MCPSessionConfig = Field(default_factory=MCPSessionConfig)
    
    # 설정 파일 경로
    config_file: Optional[Path] = None
    mcp_config_file: Path = Path("mcp-config.json")
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore"
    )
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._load_config_files()
        
    def _load_config_files(self):
        """설정 파일 로드"""
        # YAML/JSON 설정 파일 로드
        if self.config_file and self.config_file.exists():
            self._load_config_file(self.config_file)
            
        # MCP 서버 설정 파일 로드
        if self.mcp_config_file.exists():
            self._load_mcp_config()
            
    def _load_config_file(self, path: Path):
        """일반 설정 파일 로드"""
        try:
            if path.suffix in [".yaml", ".yml"]:
                import yaml
                with open(path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
            else:
                with open(path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    
            # 설정 업데이트
            for key, value in config.items():
                if hasattr(self, key):
                    if isinstance(getattr(self, key), BaseModel):
                        setattr(self, key, type(getattr(self, key))(**value))
                    else:
                        setattr(self, key, value)
                        
        except Exception as e:
            print(f"Error loading config file {path}: {e}")
            
    def _load_mcp_config(self):
        """MCP 서버 설정 파일 로드"""
        try:
            with open(self.mcp_config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
                
            mcp_servers = config.get("mcpServers", {})
            
            for server_name, server_config in mcp_servers.items():
                self.mcp_servers[server_name] = MCPServerConfig(**server_config)
                
        except Exception as e:
            print(f"Error loading MCP config file: {e}")
            
    def reload(self):
        """설정 리로드"""
        # 기존 설정 초기화
        self.mcp_servers.clear()
        
        # 설정 파일 다시 로드
        self._load_config_files()
        
    def get_mcp_server(self, name: str) -> Optional[MCPServerConfig]:
        """특정 MCP 서버 설정 조회"""
        return self.mcp_servers.get(name)
        
    def get_enabled_mcp_servers(self) -> Dict[str, MCPServerConfig]:
        """활성화된 MCP 서버 목록 조회"""
        return {
            name: config
            for name, config in self.mcp_servers.items()
            if not config.disabled
        }
        
    @classmethod
    def from_env(cls) -> "Settings":
        """환경 변수에서 설정 로드"""
        # 환경 변수 매핑
        env_mapping = {
            # 서버 설정
            "SERVER__PORT": ("server", "port"),
            "SERVER__HOST": ("server", "host"),
            "SERVER__MODE": ("server", "mode"),
            "SERVER__LOG_LEVEL": ("server", "log_level"),
            
            # 데이터베이스 설정
            "DB_HOST": ("database", "host"),
            "DB_PORT": ("database", "port"),
            "DB_USER": ("database", "user"),
            "DB_PASSWORD": ("database", "password"),
            "DB_NAME": ("database", "name"),
            "DATABASE_URL": ("database", "url"),
            "SQL_ECHO": ("database", "sql_echo"),
            
            # 로깅 설정
            "LOG_LEVEL": ("logging", "level"),
            "LOG_FORMAT": ("logging", "format"),
            "LOG_OUTPUT": ("logging", "output"),
            "LOG_FILE_PATH": ("logging", "file_path"),
            
            # 보안 설정
            "INITIAL_ADMIN_EMAIL": ("security", "initial_admin_email"),
        }
        
        kwargs = {}
        
        for env_key, path in env_mapping.items():
            value = os.getenv(env_key)
            if value is not None:
                # 중첩된 설정 처리
                current = kwargs
                for key in path[:-1]:
                    if key not in current:
                        current[key] = {}
                    current = current[key]
                    
                # API 키 특별 처리
                if env_key == "API_KEY":
                    current[path[-1]] = [{"name": "default", "key": value}]
                else:
                    current[path[-1]] = value
                    
        return cls(**kwargs)
        
    def to_dict(self) -> Dict[str, Any]:
        """설정을 딕셔너리로 변환"""
        return {
            "server": self.server.model_dump(),
            "database": self.database.model_dump(exclude={"password"}),
            "logging": self.logging.model_dump(),
            "security": self.security.model_dump(exclude={"jwt_secret"}),
            "llm": self.llm.model_dump(exclude={"azure__api_key", "openai__api_key", "anthropic__api_key"}),
            "execution": self.execution.model_dump(),
            "mcp_servers": {
                name: config.model_dump()
                for name, config in self.mcp_servers.items()
            }
        }
    
    def setup_logging(self) -> None:
        """로깅 시스템을 설정합니다."""
        from .utils.logging import setup_logging
        
        setup_logging(
            level=self.logging.level,
            format_type=self.logging.format,
            output=self.logging.output,
            file_path=self.logging.file_path
        )


# 전역 설정 인스턴스
settings = Settings.from_env()
