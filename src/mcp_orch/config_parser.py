"""
Configuration parser for MCP Orch.
Handles loading and parsing of mcp-config.json files.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""
    name: str
    command: str = ""  # stdio용, SSE는 필요없음
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    timeout: int = 60
    auto_approve: List[str] = field(default_factory=list)
    transport_type: str = "stdio"  # "stdio" 또는 "sse"
    disabled: bool = False
    
    # SSE 전용 필드
    url: Optional[str] = None  # SSE 서버 URL
    headers: Dict[str, str] = field(default_factory=dict)  # SSE 요청 헤더
    
    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> 'MCPServerConfig':
        """Create MCPServerConfig from dictionary."""
        transport_type = data.get('type', data.get('transportType', 'stdio'))  # 'type' 필드도 지원
        
        return cls(
            name=name,
            command=data.get('command', ''),
            args=data.get('args', []),
            env=data.get('env', {}),
            timeout=data.get('timeout', 60),
            auto_approve=data.get('autoApprove', []),
            transport_type=transport_type,
            disabled=data.get('disabled', False),
            # SSE 전용 필드
            url=data.get('url'),
            headers=data.get('headers', {})
        )
    
    def is_sse_server(self) -> bool:
        """SSE 서버인지 확인"""
        return self.transport_type == "sse" or self.transport_type == "http"  # http는 SSE의 별칭
    
    def is_stdio_server(self) -> bool:
        """stdio 서버인지 확인"""
        return self.transport_type == "stdio"
    
    def validate(self) -> bool:
        """설정 유효성 검증"""
        if self.is_sse_server():
            if not self.url:
                logger.error(f"SSE server '{self.name}' requires 'url' field")
                return False
            if not self.url.startswith(('http://', 'https://')):
                logger.error(f"SSE server '{self.name}' URL must start with http:// or https://")
                return False
        elif self.is_stdio_server():
            if not self.command:
                logger.error(f"stdio server '{self.name}' requires 'command' field")
                return False
        else:
            logger.error(f"Unknown transport type for server '{self.name}': {self.transport_type}")
            return False
        
        return True


@dataclass
class MCPConfig:
    """Main configuration for MCP Orch."""
    servers: Dict[str, MCPServerConfig] = field(default_factory=dict)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MCPConfig':
        """Create MCPConfig from dictionary."""
        config = cls()
        
        # Parse MCP servers - support both 'mcpServers' and 'servers' keys
        mcp_servers = data.get('mcpServers', data.get('servers', {}))
        for name, server_data in mcp_servers.items():
            if isinstance(server_data, dict):
                config.servers[name] = MCPServerConfig.from_dict(name, server_data)
        
        return config


class ConfigParser:
    """Parser for MCP configuration files."""
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the config parser.
        
        Args:
            config_path: Path to the configuration file. If None, will search for default locations.
        """
        self.config_path = self._resolve_config_path(config_path)
        self._config: Optional[MCPConfig] = None
        self._last_modified: Optional[float] = None
    
    def _resolve_config_path(self, config_path: Optional[str]) -> Path:
        """Resolve the configuration file path."""
        if config_path:
            return Path(config_path)
        
        # Search for config file in common locations
        search_paths = [
            Path.cwd() / "mcp-config.json",
            Path.home() / ".mcp" / "config.json",
            Path("/etc/mcp/config.json"),
        ]
        
        for path in search_paths:
            if path.exists():
                logger.info(f"Found configuration file at: {path}")
                return path
        
        # Default to current directory
        default_path = Path.cwd() / "mcp-config.json"
        logger.warning(f"No configuration file found. Using default path: {default_path}")
        return default_path
    
    def load(self) -> MCPConfig:
        """
        Load the configuration from file.
        
        Returns:
            MCPConfig object
            
        Raises:
            FileNotFoundError: If config file doesn't exist
            json.JSONDecodeError: If config file is invalid JSON
        """
        if not self.config_path.exists():
            logger.warning(f"Configuration file not found: {self.config_path}")
            return MCPConfig()
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self._config = MCPConfig.from_dict(data)
            self._last_modified = os.path.getmtime(self.config_path)
            
            logger.info(f"Loaded configuration with {len(self._config.servers)} servers")
            return self._config
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in configuration file: {e}")
            raise
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            raise
    
    def reload_if_changed(self) -> bool:
        """
        Reload configuration if the file has been modified.
        
        Returns:
            True if configuration was reloaded, False otherwise
        """
        if not self.config_path.exists():
            return False
        
        current_mtime = os.path.getmtime(self.config_path)
        if self._last_modified is None or current_mtime > self._last_modified:
            logger.info("Configuration file changed, reloading...")
            try:
                self.load()
                return True
            except Exception as e:
                logger.error(f"Error reloading configuration: {e}")
                return False
        
        return False
    
    def get_active_servers(self) -> Dict[str, MCPServerConfig]:
        """
        Get all active (non-disabled) server configurations.
        
        Returns:
            Dictionary of active server configurations
        """
        if not self._config:
            self.load()
        
        return {
            name: config 
            for name, config in self._config.servers.items() 
            if not config.disabled
        }
    
    def save_example(self, path: Optional[str] = None) -> None:
        """
        Save an example configuration file.
        
        Args:
            path: Path to save the example file. If None, uses 'mcp-config.example.json'
        """
        example_path = Path(path) if path else Path("mcp-config.example.json")
        
        example_config = {
            "mcpServers": {
                # stdio 방식 서버 예시
                "github-server": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {
                        "GITHUB_TOKEN": "your-github-token"
                    },
                    "timeout": 60,
                    "autoApprove": ["list_issues", "create_issue"],
                    "type": "stdio",  # "stdio" 또는 "sse"
                    "disabled": False
                },
                # SSE 방식 서버 예시
                "remote-sse-server": {
                    "url": "http://10.150.0.36:8000/mcp",
                    "type": "sse",
                    "timeout": 30,
                    "headers": {
                        "Authorization": "Bearer your-api-token",
                        "X-Custom-Header": "value"
                    },
                    "autoApprove": ["safe_tool1", "safe_tool2"],
                    "disabled": False
                },
                "notion-server": {
                    "command": "node",
                    "args": ["/path/to/notion-server"],
                    "env": {
                        "NOTION_API_KEY": "your-notion-api-key"
                    },
                    "type": "stdio",
                    "disabled": True
                },
                "local-server": {
                    "command": "python",
                    "args": ["-m", "my_mcp_server"],
                    "env": {
                        "SERVER_PORT": "8080"
                    },
                    "timeout": 30,
                    "autoApprove": [],
                    "transportType": "stdio",
                    "disabled": False
                }
            }
        }
        
        with open(example_path, 'w', encoding='utf-8') as f:
            json.dump(example_config, f, indent=2)
        
        logger.info(f"Example configuration saved to: {example_path}")


# Convenience functions
def load_config(config_path: Optional[str] = None) -> MCPConfig:
    """
    Load MCP configuration from file.
    
    Args:
        config_path: Optional path to configuration file
        
    Returns:
        MCPConfig object
    """
    parser = ConfigParser(config_path)
    return parser.load()


def load_mcp_config(config_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Load MCP configuration as raw dictionary for backward compatibility.
    
    Args:
        config_path: Optional path to configuration file
        
    Returns:
        Dictionary containing the raw configuration or None if not found
    """
    try:
        parser = ConfigParser(config_path)
        if not parser.config_path.exists():
            return None
            
        with open(parser.config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading MCP config: {e}")
        return None


def create_example_config(path: Optional[str] = None) -> None:
    """
    Create an example configuration file.
    
    Args:
        path: Optional path for the example file
    """
    parser = ConfigParser()
    parser.save_example(path)
