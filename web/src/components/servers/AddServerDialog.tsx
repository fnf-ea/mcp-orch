'use client';

import { useState, useEffect } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { X, Plus, FileText, Settings } from 'lucide-react';
import { Switch } from '@/components/ui/switch';
import { showAlert, showSuccess, showError } from '@/lib/dialog-utils';
// import { useToast } from '@/hooks/use-toast'; // TODO: Enable after implementing toast system

// Individual server form component
function IndividualServerForm({ 
  formData, 
  updateField, 
  newArg, 
  setNewArg, 
  addArg, 
  removeArg, 
  newEnvKey, 
  setNewEnvKey, 
  newEnvValue, 
  setNewEnvValue, 
  addEnvVar, 
  removeEnvVar,
  newHeaderKey,
  setNewHeaderKey,
  newHeaderValue,
  setNewHeaderValue,
  addHeader,
  removeHeader,
}: {
  formData: ServerConfig;
  updateField: (field: keyof ServerConfig, value: any) => void;
  newArg: string;
  setNewArg: (value: string) => void;
  addArg: () => void;
  removeArg: (index: number) => void;
  newEnvKey: string;
  setNewEnvKey: (value: string) => void;
  newEnvValue: string;
  setNewEnvValue: (value: string) => void;
  addEnvVar: () => void;
  removeEnvVar: (key: string) => void;
  newHeaderKey: string;
  setNewHeaderKey: (value: string) => void;
  newHeaderValue: string;
  setNewHeaderValue: (value: string) => void;
  addHeader: () => void;
  removeHeader: (key: string) => void;
}) {
  return (
    <div className="space-y-6">
      {/* Basic Information */}
      <div className="space-y-4">
        <h3 className="text-sm font-medium">Basic Information</h3>
        
        <div className="space-y-2">
          <Label htmlFor="name">Server Name *</Label>
          <Input
            id="name"
            value={formData.name}
            onChange={(e) => updateField('name', e.target.value)}
            placeholder="e.g., github-server, filesystem-server"
            required
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="description">Description</Label>
          <Textarea
            id="description"
            value={formData.description}
            onChange={(e) => updateField('description', e.target.value)}
            placeholder="Describe the server's role and functionality"
            rows={2}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="transport">Transport Method</Label>
          <Select value={formData.transport} onValueChange={(value: 'stdio' | 'sse') => updateField('transport', value)}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="stdio">Standard I/O</SelectItem>
              <SelectItem value="sse">Server-Sent Events (HTTP)</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {/* SSE 전용 필드들 */}
        {formData.transport === 'sse' && (
          <>
            <div className="space-y-2">
              <Label htmlFor="url">SSE Server URL *</Label>
              <Input
                id="url"
                value={formData.url || ''}
                onChange={(e) => updateField('url', e.target.value)}
                placeholder="e.g., http://localhost:8080/mcp"
                required={formData.transport === 'sse'}
              />
            </div>

            <div className="space-y-2">
              <Label>HTTP Headers (Optional)</Label>
              <div className="flex gap-2">
                <Input
                  value={newHeaderKey}
                  onChange={(e) => setNewHeaderKey(e.target.value)}
                  placeholder="Header Name"
                  className="flex-1"
                />
                <Input
                  value={newHeaderValue}
                  onChange={(e) => setNewHeaderValue(e.target.value)}
                  placeholder="Header Value"
                  className="flex-1"
                  onKeyPress={(e) => e.key === 'Enter' && (e.preventDefault(), addHeader())}
                />
                <Button type="button" onClick={addHeader} size="sm">
                  <Plus className="h-4 w-4" />
                </Button>
              </div>
              
              {Object.entries(formData.headers || {}).length > 0 && (
                <div className="space-y-2">
                  {Object.entries(formData.headers || {}).map(([key, value]) => (
                    <div key={key} className="flex items-center justify-between p-2 bg-muted rounded">
                      <span className="text-sm font-mono">
                        <strong>{key}</strong>: {value}
                      </span>
                      <X 
                        className="h-4 w-4 cursor-pointer hover:text-red-500" 
                        onClick={() => removeHeader(key)}
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}

        {/* Compatibility Mode는 Resource Connection으로 고정됨 */}
      </div>

      {/* JWT Authentication Settings */}
      <div className="space-y-4">
        <h3 className="text-sm font-medium">Authentication Settings</h3>
        
        <div className="space-y-3">
          <div className="flex items-center justify-between p-3 border rounded-lg">
            <div className="space-y-1">
              <Label htmlFor="jwt-auth-toggle" className="text-sm font-medium">
                JWT Authentication Required
              </Label>
              <p className="text-xs text-muted-foreground">
                Override project default authentication setting for this server
              </p>
            </div>
            <div className="flex items-center space-x-2">
              <span className="text-xs text-muted-foreground">
                {formData.jwt_auth_required === null ? 'Project Default' : 
                 formData.jwt_auth_required ? 'Required' : 'Disabled'}
              </span>
              <Select 
                value={formData.jwt_auth_required === null ? 'inherit' : 
                       formData.jwt_auth_required ? 'required' : 'disabled'} 
                onValueChange={(value) => {
                  const newValue = value === 'inherit' ? null : 
                                 value === 'required' ? true : false;
                  updateField('jwt_auth_required', newValue);
                }}
              >
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="inherit">Inherit</SelectItem>
                  <SelectItem value="required">Required</SelectItem>
                  <SelectItem value="disabled">Disabled</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>
      </div>

      {/* Execution Settings - stdio 전용 */}
      {formData.transport === 'stdio' && (
        <div className="space-y-4">
          <h3 className="text-sm font-medium">Execution Settings</h3>
          
          <div className="space-y-2">
            <Label htmlFor="command">Command *</Label>
            <Input
              id="command"
              value={formData.command}
              onChange={(e) => updateField('command', e.target.value)}
              placeholder="e.g., python, node, uvx, /usr/local/bin/my-server"
              required={formData.transport === 'stdio'}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="cwd">Working Directory</Label>
            <Input
              id="cwd"
              value={formData.cwd || ''}
              onChange={(e) => updateField('cwd', e.target.value)}
              placeholder="e.g., /path/to/server (current directory if empty)"
            />
          </div>

          {/* Arguments */}
          <div className="space-y-2">
            <Label>Command Arguments</Label>
            <div className="flex gap-2">
              <Input
                value={newArg}
                onChange={(e) => setNewArg(e.target.value)}
                placeholder="Enter argument and click Add button"
                onKeyPress={(e) => e.key === 'Enter' && (e.preventDefault(), addArg())}
              />
              <Button type="button" onClick={addArg} size="sm">
                <Plus className="h-4 w-4" />
              </Button>
            </div>
            {formData.args.length > 0 && (
              <div className="flex flex-wrap gap-2 mt-2">
                {formData.args.map((arg, index) => (
                  <Badge key={index} variant="secondary" className="flex items-center gap-1">
                    {arg}
                    <X 
                      className="h-3 w-3 cursor-pointer hover:text-red-500" 
                      onClick={() => removeArg(index)}
                    />
                  </Badge>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Environment Variables */}
      <div className="space-y-4">
        <h3 className="text-sm font-medium">Environment Variables</h3>
        
        <div className="flex gap-2">
          <Input
            value={newEnvKey}
            onChange={(e) => setNewEnvKey(e.target.value)}
            placeholder="Variable Name"
            className="flex-1"
          />
          <Input
            value={newEnvValue}
            onChange={(e) => setNewEnvValue(e.target.value)}
            placeholder="Value"
            className="flex-1"
            onKeyPress={(e) => e.key === 'Enter' && (e.preventDefault(), addEnvVar())}
          />
          <Button type="button" onClick={addEnvVar} size="sm">
            <Plus className="h-4 w-4" />
          </Button>
        </div>
        
        {Object.entries(formData.env).length > 0 && (
          <div className="space-y-2">
            {Object.entries(formData.env).map(([key, value]) => (
              <div key={key} className="flex items-center justify-between p-2 bg-muted rounded">
                <span className="text-sm font-mono">
                  <strong>{key}</strong> = {value}
                </span>
                <X 
                  className="h-4 w-4 cursor-pointer hover:text-red-500" 
                  onClick={() => removeEnvVar(key)}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// JSON editing/adding form component
function JsonBulkAddForm({ 
  jsonConfig, 
  setJsonConfig, 
  onJsonSubmit,
  isLoading,
  isEditMode = false
}: {
  jsonConfig: string;
  setJsonConfig: (value: string) => void;
  onJsonSubmit: () => void;
  isLoading: boolean;
  isEditMode?: boolean;
}) {

  // JSON 예시 설정 (stdio와 SSE 예시 포함)
  const exampleConfig = `{
  "brave-search": {
    "disabled": false,
    "timeout": 60,
    "type": "stdio",
    "command": "npx",
    "args": [
      "-y",
      "@modelcontextprotocol/server-brave-search"
    ],
    "env": {
      "BRAVE_API_KEY": "your-brave-api-key-here"
    }
  },
  "sse-example": {
    "disabled": false,
    "timeout": 30,
    "type": "sse",
    "url": "http://localhost:8080/mcp",
    "headers": {
      "X-API-Key": "your-api-key"
    }
  }
}`;

  return (
    <div className="space-y-6">
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium">
            {isEditMode ? 'Edit Server Settings JSON' : 'MCP Settings JSON'}
          </h3>
          {!isEditMode && (
            <Button 
              type="button" 
              variant="outline" 
              size="sm"
              onClick={() => setJsonConfig(exampleConfig)}
            >
              Load Example
            </Button>
          )}
        </div>
        
        <div className="space-y-2">
          <Label htmlFor="jsonConfig">JSON Settings *</Label>
          <Textarea
            id="jsonConfig"
            value={jsonConfig}
            onChange={(e) => setJsonConfig(e.target.value)}
            placeholder={isEditMode ? 
              "Current server settings are displayed in JSON format. Modify the necessary parts..." : 
              "Paste your server settings JSON here..."
            }
            rows={15}
            className="font-mono text-sm"
          />
        </div>

        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
          <h4 className="font-medium text-blue-900 mb-2">Usage Instructions</h4>
          <ul className="text-sm text-blue-700 space-y-1">
            {isEditMode ? (
              <>
                <li>• Current server settings are displayed in JSON format</li>
                <li>• Directly modify the necessary parts and save</li>
                <li>• You can edit server name, command, environment variables, etc.</li>
                <li>• Please ensure the JSON format is valid</li>
              </>
            ) : (
              <>
                <li>• Paste existing MCP settings or server configurations only</li>
                <li>• Use "Load Example" button to see the simple format</li>
                <li>• Supports "serverName": {`{"disabled": false, "command": "npx", ...}`} format</li>
                <li>• mcpServers wrapper is automatically handled</li>
                <li>• Multiple servers can be added at once</li>
              </>
            )}
          </ul>
        </div>

        <Button 
          onClick={onJsonSubmit} 
          disabled={isLoading || !jsonConfig.trim()}
          className="w-full"
        >
          {isLoading ? 
            (isEditMode ? 'Updating Server...' : 'Adding Servers...') : 
            (isEditMode ? 'Update Server with JSON Settings' : 'Bulk Add Servers from JSON')
          }
        </Button>
      </div>
    </div>
  );
}

interface ServerConfig {
  name: string;
  description: string;
  transport: 'stdio' | 'sse';
  command: string;
  args: string[];
  env: Record<string, string>;
  cwd?: string;
  jwt_auth_required?: boolean | null;  // null = inherit from project
  url?: string;  // SSE 서버용 URL
  headers?: Record<string, string>;  // SSE 서버용 HTTP 헤더
}

interface MarketplaceServerConfig {
  id: string;
  name: string;
  description: string;
  category: string;
  config: {
    command: string;
    args: string[];
    env_template: Record<string, string>;
    timeout: number;
    transport: string;
  };
  setup: {
    required_env: string[];
    setup_guide: string;
  };
}

interface AddServerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onServerAdded: (server: ServerConfig) => void;
  onServerUpdated?: (server: ServerConfig) => void;
  projectId: string;
  editServer?: {
    id: string;
    name: string;
    description?: string;
    transport?: 'stdio' | 'sse';
    command?: string;
    args?: string[];
    env?: Record<string, string>;
    cwd?: string;
    jwt_auth_required?: boolean | null;
    url?: string;  // SSE 서버용 URL
    headers?: Record<string, string>;  // SSE 서버용 헤더
  };
  marketplaceConfig?: MarketplaceServerConfig;
}

export function AddServerDialog({ 
  open, 
  onOpenChange, 
  onServerAdded, 
  onServerUpdated,
  projectId,
  editServer,
  marketplaceConfig
}: AddServerDialogProps) {
  // const { toast } = useToast(); // TODO: 토스트 시스템 구현 후 활성화
  const [isLoading, setIsLoading] = useState(false);
  const isEditMode = !!editServer;
  const [activeTab, setActiveTab] = useState('individual');
  
  // 폼 상태
  const [formData, setFormData] = useState<ServerConfig>({
    name: '',
    description: '',
    transport: 'stdio',
    command: '',
    args: [],
    env: {},
    cwd: '',
    jwt_auth_required: null,  // null = inherit from project
    url: '',  // SSE 서버용
    headers: {}  // SSE 서버용
  });
  
  // JSON 일괄 추가 상태
  const [jsonConfig, setJsonConfig] = useState('');
  
  // 임시 입력 상태
  const [newArg, setNewArg] = useState('');
  const [newEnvKey, setNewEnvKey] = useState('');
  const [newEnvValue, setNewEnvValue] = useState('');
  const [newHeaderKey, setNewHeaderKey] = useState('');  // SSE 헤더용
  const [newHeaderValue, setNewHeaderValue] = useState('');  // SSE 헤더용
  

  // 입력값 업데이트
  const updateField = (field: keyof ServerConfig, value: any) => {
    setFormData(prev => ({ ...prev, [field]: value }));
  };

  // 인자 추가
  const addArg = () => {
    if (newArg.trim()) {
      updateField('args', [...formData.args, newArg.trim()]);
      setNewArg('');
    }
  };

  // 인자 제거
  const removeArg = (index: number) => {
    updateField('args', formData.args.filter((_, i) => i !== index));
  };

  // 환경 변수 추가
  const addEnvVar = () => {
    if (newEnvKey.trim() && newEnvValue.trim()) {
      updateField('env', { ...formData.env, [newEnvKey.trim()]: newEnvValue.trim() });
      setNewEnvKey('');
      setNewEnvValue('');
    }
  };

  // 환경 변수 제거
  const removeEnvVar = (key: string) => {
    const newEnv = { ...formData.env };
    delete newEnv[key];
    updateField('env', newEnv);
  };

  // HTTP 헤더 추가 (SSE용)
  const addHeader = () => {
    if (newHeaderKey.trim() && newHeaderValue.trim()) {
      updateField('headers', { ...formData.headers, [newHeaderKey.trim()]: newHeaderValue.trim() });
      setNewHeaderKey('');
      setNewHeaderValue('');
    }
  };

  // HTTP 헤더 제거 (SSE용)
  const removeHeader = (key: string) => {
    const newHeaders = { ...formData.headers };
    delete newHeaders[key];
    updateField('headers', newHeaders);
  };

  // 서버 설정을 JSON으로 변환 (편집 모드용)
  const convertServerToJson = (serverConfig: ServerConfig) => {
    const baseConfig: any = {
      disabled: false,
      timeout: 30,
      type: serverConfig.transport === 'sse' ? 'sse' : 'stdio',
      ...(serverConfig.description && { description: serverConfig.description })
    };

    // Transport별 필드 추가
    if (serverConfig.transport === 'sse') {
      baseConfig.url = serverConfig.url || '';
      if (Object.keys(serverConfig.headers || {}).length > 0) {
        baseConfig.headers = serverConfig.headers;
      }
    } else {
      baseConfig.command = serverConfig.command;
      baseConfig.args = serverConfig.args || [];
      if (Object.keys(serverConfig.env || {}).length > 0) {
        baseConfig.env = serverConfig.env;
      }
      if (serverConfig.cwd) {
        baseConfig.cwd = serverConfig.cwd;
      }
    }

    const mcpServerConfig = {
      [serverConfig.name]: baseConfig
    };
    
    return JSON.stringify({ mcpServers: mcpServerConfig }, null, 2);
  };

  // 마켓플레이스 설정을 JSON으로 변환
  const convertMarketplaceToJson = (marketplaceServer: MarketplaceServerConfig) => {
    const mcpServerConfig = {
      [marketplaceServer.name]: {
        disabled: false,
        timeout: marketplaceServer.config.timeout,
        type: marketplaceServer.config.transport,
        command: marketplaceServer.config.command,
        args: marketplaceServer.config.args,
        env: marketplaceServer.config.env_template,
        ...(marketplaceServer.description && { description: marketplaceServer.description })
      }
    };
    
    return JSON.stringify({ mcpServers: mcpServerConfig }, null, 2);
  };

  // 편집 모드 또는 마켓플레이스 모드일 때 폼 데이터 초기화
  useEffect(() => {
    if (editServer) {
      console.log('=== Edit Server Dialog Opened ===');
      console.log('Received editServer data:', editServer);
      console.log('editServer properties:', {
        id: editServer.id,
        name: editServer.name,
        description: editServer.description,
        transport: editServer.transport,
        command: editServer.command,
        args: editServer.args,
        env: editServer.env,
        cwd: editServer.cwd,
        jwt_auth_required: editServer.jwt_auth_required,
        url: editServer.url,
        headers: editServer.headers
      });
      
      const serverConfig: ServerConfig = {
        name: editServer.name,
        description: editServer.description || '',
        transport: editServer.transport || 'stdio',
        command: editServer.command || '',
        args: editServer.args || [],
        env: editServer.env || {},
        cwd: editServer.cwd || '',
        jwt_auth_required: editServer.jwt_auth_required ?? null,
        url: editServer.url || '',
        headers: editServer.headers || {}
      };
      
      console.log('Converted serverConfig:', serverConfig);
      
      setFormData(serverConfig);
      
      // 편집 모드일 때 JSON 탭을 현재 서버 설정으로 초기화
      setJsonConfig(convertServerToJson(serverConfig));
    } else if (marketplaceConfig) {
      // 마켓플레이스 모드일 때
      resetForm();
      setActiveTab('json'); // JSON 탭으로 자동 전환
      setJsonConfig(convertMarketplaceToJson(marketplaceConfig));
    } else {
      resetForm();
      setJsonConfig('');
    }
  }, [editServer, marketplaceConfig, open]);

  // 폼 초기화
  const resetForm = () => {
    setFormData({
      name: '',
      description: '',
      transport: 'stdio',
      command: '',
      args: [],
      env: {},
      cwd: '',
      jwt_auth_required: null,
      url: '',
      headers: {}
    });
    setNewArg('');
    setNewEnvKey('');
    setNewEnvValue('');
    setNewHeaderKey('');
    setNewHeaderValue('');
  };

  // 서버 추가/수정 처리
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    // transport type에 따른 검증
    if (formData.transport === 'sse') {
      if (!formData.name.trim() || !formData.url?.trim()) {
        await showError("서버 이름과 URL은 필수 입력 항목입니다.");
        return;
      }
    } else {
      if (!formData.name.trim() || !formData.command.trim()) {
        await showError("서버 이름과 명령어는 필수 입력 항목입니다.");
        return;
      }
    }

    setIsLoading(true);

    try {
      if (isEditMode && editServer) {
        // 서버 수정 API 호출
        const requestBody = {
          name: formData.name,
          description: formData.description,
          transport_type: formData.transport,  // Changed from 'transport' to 'transport_type'
          command: formData.command,
          args: formData.args,
          env: formData.env,
          cwd: formData.cwd || null,
          jwt_auth_required: formData.jwt_auth_required,
          // SSE 서버인 경우 추가 필드
          ...(formData.transport === 'sse' && {
            url: formData.url,
            headers: formData.headers
          })
        };
        
        console.log('=== Sending Server Update Request ===');
        console.log('API URL:', `/api/projects/${projectId}/servers/${editServer.id}`);
        console.log('Request body:', requestBody);
        
        const response = await fetch(`/api/projects/${projectId}/servers/${editServer.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(requestBody),
          credentials: 'include'
        });
        
        console.log('Response status:', response.status);
        console.log('Response OK:', response.ok);
        
        if (!response.ok) {
          const errorData = await response.json();
          console.error('Server update error response:', errorData);
          throw new Error(errorData.error || 'Server update failed');
        }

        const result = await response.json();
        console.log('Server update successful response:', result);
        console.log('서버 수정 성공:', result);
        
        onServerUpdated?.(formData);
      } else {
        // 서버 추가 API 호출
        const requestBody: any = {
          name: formData.name,
          description: formData.description,
          transport_type: formData.transport,
          jwt_auth_required: formData.jwt_auth_required
        };

        // transport type에 따라 다른 필드 설정
        if (formData.transport === 'sse') {
          requestBody.url = formData.url;
          requestBody.headers = formData.headers;
          requestBody.timeout = 30;  // SSE 기본 타임아웃
        } else {
          requestBody.command = formData.command;
          requestBody.args = formData.args;
          requestBody.env = formData.env;
          requestBody.cwd = formData.cwd || null;
          requestBody.timeout = 60;  // stdio 기본 타임아웃
        }

        const response = await fetch(`/api/projects/${projectId}/servers`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(requestBody),
          credentials: 'include'
        });

        if (!response.ok) {
          const errorData = await response.json();
          throw new Error(errorData.error || 'Server addition failed');
        }

        const result = await response.json();
        console.log('서버 추가 성공:', result);
        
        onServerAdded(formData);
      }

      const serverName = formData.name;
      resetForm();
      onOpenChange(false);
      
      // 다이얼로그가 닫힌 후에 성공 메시지 표시
      if (isEditMode) {
        await showSuccess(`서버 업데이트 완료: ${serverName} 서버가 성공적으로 업데이트되었습니다.`);
      } else {
        await showSuccess(`서버 추가 완료: ${serverName} 서버가 성공적으로 추가되었습니다.`);
      }
      
    } catch (error) {
      console.error(`서버 ${isEditMode ? '수정' : '추가'} 오류:`, error);
      await showError(`서버 ${isEditMode ? '업데이트' : '추가'} 실패: ${error instanceof Error ? error.message : '알 수 없는 오류가 발생했습니다.'}`);
    } finally {
      setIsLoading(false);
    }
  };

  // JSON 추가/수정 처리
  const handleJsonSubmit = async () => {
    if (!jsonConfig.trim()) {
      await showError('JSON 설정을 입력해주세요.');
      return;
    }

    try {
      const config = JSON.parse(jsonConfig);
      
      // 입력된 JSON이 이미 mcpServers 래퍼를 가지고 있는지 확인
      let mcpServers;
      if (config.mcpServers && typeof config.mcpServers === 'object') {
        // 기존 형식: mcpServers 래퍼가 있음
        mcpServers = config.mcpServers;
      } else if (typeof config === 'object' && config !== null) {
        // 새로운 형식: 서버 설정만 있음 - 자동으로 래퍼 추가
        mcpServers = config;
      } else {
        throw new Error('Invalid MCP settings format.');
      }

      // JSON 설정 그대로 사용 (compatibility_mode 자동 추가 제거)

      setIsLoading(true);

      // 편집 모드일 때
      if (isEditMode && editServer) {
        const servers = Object.entries(mcpServers);
        if (servers.length !== 1) {
          throw new Error('In edit mode, please enter only one server configuration.');
        }

        const [serverName, serverConfig] = servers[0];
        const server = serverConfig as any;

        // SSE와 stdio 서버 구분하여 처리
        const updateBody: any = {
          name: serverName,
          description: server.description || '',
          transport_type: server.type === 'sse' ? 'sse' : 'stdio',  // transport_type 사용
          timeout: server.timeout || 30
        };

        // Transport type에 따른 필드 설정
        if (server.type === 'sse') {
          // SSE 서버 필드
          updateBody.url = server.url || '';
          updateBody.headers = server.headers || {};
        } else {
          // stdio 서버 필드
          updateBody.command = server.command || '';
          updateBody.args = server.args || [];
          updateBody.env = server.env || {};
          if (server.cwd) {
            updateBody.cwd = server.cwd;
          }
        }

        const response = await fetch(`/api/projects/${projectId}/servers/${editServer.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(updateBody),
          credentials: 'include'
        });

        if (!response.ok) {
          const errorData = await response.json();
          throw new Error(errorData.error || 'Server update failed');
        }

        const result = await response.json();
        console.log('서버 수정 성공:', result);
        
        onServerUpdated?.({
          name: serverName,
          description: server.description || '',
          transport: server.type === 'sse' ? 'sse' : 'stdio',
          command: server.command || '',
          args: server.args || [],
          env: server.env || {},
          cwd: server.cwd || ''
        });
        
        onOpenChange(false);
        await showSuccess(`서버 업데이트 완료: ${serverName} 서버가 성공적으로 업데이트되었습니다.`);
        return;
      }

      // 추가 모드일 때 (기존 로직)
      const servers = Object.entries(mcpServers);
      let successCount = 0;
      let errorCount = 0;
      const errors: string[] = [];

      for (const [serverName, serverConfig] of servers) {
        try {
          const server = serverConfig as any;
          
          // 요청 본문 구성
          const requestBody: any = {
            name: serverName,
            description: server.description || `${serverName} MCP server`,
            transport_type: server.type || 'stdio',
            is_enabled: !server.disabled
          };

          // transport type에 따라 다른 필드 설정
          if (server.type === 'sse' || server.type === 'http') {
            requestBody.url = server.url;
            requestBody.headers = server.headers || {};
            requestBody.timeout = server.timeout || 30;
          } else {
            requestBody.command = server.command;
            requestBody.args = server.args || [];
            requestBody.env = server.env || {};
            requestBody.cwd = server.cwd || null;
            requestBody.timeout = server.timeout || 60;
          }
          
          const response = await fetch(`/api/projects/${projectId}/servers`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody),
            credentials: 'include'
          });

          if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.error || 'Server addition failed');
          }

          successCount++;
          
          // 콜백 호출 (UI 업데이트용)
          onServerAdded({
            name: serverName,
            description: server.description || `${serverName} MCP 서버`,
            transport: server.type === 'sse' ? 'sse' : 'stdio',
            command: server.command,
            args: server.args || [],
            env: server.env || {},
            cwd: server.cwd
          });

        } catch (error) {
          errorCount++;
          errors.push(`${serverName}: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
      }

      // 결과 메시지
      if (successCount > 0 && errorCount === 0) {
        setJsonConfig('');
        onOpenChange(false);
        await showSuccess(`성공: 모든 서버 ${successCount}개가 추가되었습니다.`);
      } else if (successCount > 0 && errorCount > 0) {
        await showAlert({ 
          title: '부분 성공', 
          message: `${successCount}개 서버가 성공적으로 추가되었으며, ${errorCount}개가 실패했습니다.\n\n실패한 서버들:\n${errors.join('\n')}` 
        });
      } else {
        await showError(`실패: 모든 서버 추가가 실패했습니다.\n\n오류 목록:\n${errors.join('\n')}`);
      }

    } catch (error) {
      console.error('JSON 파싱 오류:', error);
      await showError(`JSON 형식 오류: ${error instanceof Error ? error.message : '잘못된 JSON 형식입니다.'}`);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEditMode ? 'Edit MCP Server Settings' : 'Add New MCP Server'}</DialogTitle>
          <DialogDescription>
            {isEditMode 
              ? 'Modify server settings. Please update the fields you want to change.'
              : 'Add a new MCP server to the project. Please enter all fields accurately.'
            }
          </DialogDescription>
        </DialogHeader>

        {/* 편집 모드와 추가 모드 모두 탭 표시 */}
        <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="json" className="flex items-center gap-2">
              <FileText className="h-4 w-4" />
              {isEditMode ? 'JSON Edit' : 'JSON Add'}
            </TabsTrigger>
            <TabsTrigger value="individual" className="flex items-center gap-2">
              <Settings className="h-4 w-4" />
              {isEditMode ? 'Individual Edit' : 'Individual Add'}
            </TabsTrigger>
          </TabsList>

            {/* 개별 추가 탭 */}
            <TabsContent value="individual" className="space-y-6">
              <form onSubmit={handleSubmit}>
                <IndividualServerForm 
                  formData={formData}
                  updateField={updateField}
                  newArg={newArg}
                  setNewArg={setNewArg}
                  addArg={addArg}
                  removeArg={removeArg}
                  newEnvKey={newEnvKey}
                  setNewEnvKey={setNewEnvKey}
                  newEnvValue={newEnvValue}
                  setNewEnvValue={setNewEnvValue}
                  addEnvVar={addEnvVar}
                  removeEnvVar={removeEnvVar}
                  newHeaderKey={newHeaderKey}
                  setNewHeaderKey={setNewHeaderKey}
                  newHeaderValue={newHeaderValue}
                  setNewHeaderValue={setNewHeaderValue}
                  addHeader={addHeader}
                  removeHeader={removeHeader}
                />
              </form>
            </TabsContent>

            {/* JSON 편집/추가 탭 */}
            <TabsContent value="json" className="space-y-6">
              <JsonBulkAddForm 
                jsonConfig={jsonConfig}
                setJsonConfig={setJsonConfig}
                onJsonSubmit={handleJsonSubmit}
                isLoading={isLoading}
                isEditMode={isEditMode}
              />
            </TabsContent>
        </Tabs>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          {/* Show submit button only when not in JSON tab */}
          {(isEditMode || activeTab === 'individual') && (
            <Button type="submit" onClick={handleSubmit} disabled={isLoading}>
              {isLoading 
                ? (isEditMode ? 'Updating...' : 'Adding...') 
                : (isEditMode ? 'Update Server' : 'Add Server')
              }
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
