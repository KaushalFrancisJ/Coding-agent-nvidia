# NVIDIA NIM Coding Agent Runbook

Last updated: 2026-07-23

## Overview

This runbook documents patterns, anti-patterns, and procedures for the NVIDIA NIM Coding Agent project. The project has two codebases:
- **Root (`app.py`, `agent.py`, `tools.py`, `mcp_client.py`)**: Legacy Streamlit app with tight coupling
- **`src/chatbot/`**: Clean architecture with domain/models/application layers (RECOMMENDED)

**Decision**: Use `src/chatbot/` as the canonical implementation. Migrate root files to use it.

---

## Patterns That Work

### 1. Clean Architecture with Protocols (src/)

**Context**: Building extensible agent systems

**Pattern**: Define protocols for LLMProvider, ToolProvider, SessionStore. Implement concrete classes.

**Example**:
```python
# domain/models.py
class ToolProvider(Protocol):
    async def get_tools(self) -> List[ToolDefinition]: ...
    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> str: ...

class LLMProvider(Protocol):
    async def chat_completion(
        self, messages: List[Message], tools: List[ToolDefinition], ...
    ) -> Message: ...

# application/agent.py
class NIMAgent:
    def __init__(self, config: AgentConfig):
        self.llm = NIMLLMProvider(...)
        self.workspace = Workspace(...)  # implements ToolProvider
        self.mcp = MCPClientManager(...)  # implements ToolProvider
        self.executor = ToolExecutor([self.workspace, self.mcp])
```

**Learned from**: src/ architecture design

### 2. Sandboxed Workspace for File Operations

**Context**: File operations must not escape project directory

**Pattern**: Resolve all paths relative to workspace root, validate before every operation

**Example**:
```python
# domain/workspace.py
def _safe_path(self, path: str) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(self.root):
        raise ValueError(f"Path '{path}' escapes workspace root")
    return resolved

def read_file(self, file_path: str) -> str:
    safe_path = self._safe_path(file_path)
    # ... read with error handling
```

**Anti-Pattern Fixed**: Root `tools.py` used `Path.resolve()` which follows symlinks (CVE risk)

**Learned from**: Security review 2026-07-23

### 3. MCP Client Lifecycle Management

**Context**: MCP servers need proper connection/disconnection

**Pattern**: Async context manager with connection pooling and cleanup

**Example**:
```python
# infrastructure/mcp/client.py
class MCPClientManager:
    async def __aenter__(self):
        await self.connect_all()
        return self
    
    async def __aexit__(self, *args):
        await self.cleanup()
    
    async def cleanup(self):
        for name, stack in self.exit_stacks.items():
            await stack.aclose()
```

**Learned from**: MCP SDK patterns, src/ implementation

### 4. Pydantic Settings for Configuration

**Context**: Centralized, validated configuration

**Pattern**: Use `BaseSettings` with `SettingsConfigDict`

**Example**:
```python
# config.py
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    nvidia_api_key: str = ""
    nim_model: str = "meta/llama-3.1-70b-instruct"
    workspace_root: Path = Path(".").resolve()
```

**Learned from**: src/config.py vs root's scattered os.environ.get()

---

## Anti-Patterns

### 1. Dual Codebase Maintenance

**Problem**: Two complete implementations (root + src/) with different patterns

**Why it's bad**: 
- Duplicate bugs
- Confusion on which to use
- Inconsistent behavior
- Double maintenance burden

**Instead**: Migrate root to use src/ completely. Delete root agent.py, tools.py, mcp_client.py

**Learned from**: Code review 2026-07-23

### 2. `Path.resolve()` for Security Checks

**Problem**: `Path.resolve()` follows symlinks, allowing escape via symlink attack

**Why it's bad**: Attacker creates symlink inside workspace pointing outside; resolve() follows it

**Instead**: Use `Path(path).absolute()` and check `is_relative_to(workspace_root.absolute())`

**Learned from**: Security audit 2026-07-23 (CVE pattern)

### 3. Interpreters in Command Allowlist

**Problem**: `python`, `node`, `bash`, `npx` in allowed commands enables arbitrary code execution

**Why it's bad**: `python -c "import os; os.system('rm -rf /')"` passes validation

**Instead**: 
- Remove interpreters from allowlist
- Use specific command templates
- Or run in sandboxed container

**Learned from**: Security audit 2026-07-23

### 4. `unsafe_allow_html=True` with User Content

**Problem**: Tool names/args interpolated into HTML without escaping

**Why it's bad**: XSS via tool name `<script>alert(1)</script>`

**Instead**: Use `st.code()`, `st.json()`, `st.markdown()` with safe content

**Learned from**: Security audit 2026-07-23

### 5. Async in Streamlit Callbacks Without Proper Handling

**Problem**: `asyncio.run()` in button callbacks blocks event loop; `st.rerun()` after async loses state

**Why it's bad**: Deadlocks, lost updates, connection leaks

**Instead**: Use `st.experimental_fragment` for async loops, or run agent in background thread with queue

**Learned from**: Streamlit async patterns, code review 2026-07-23

### 6. Error Strings Instead of Exceptions

**Problem**: Functions return `"Error: ..."` strings instead of raising

**Why it's bad**: Callers forget to check, errors silent, no stack trace

**Instead**: Raise typed exceptions (`WorkspaceError`, `MCPConnectionError`, `ToolExecutionError`)

**Learned from**: Root tools.py vs src workspace.py comparison

---

## Service-Specific Notes

### NVIDIA NIM API
- Uses OpenAI-compatible endpoint at `https://integrate.api.nvidia.com/v1`
- Models: `meta/llama-3.1-70b-instruct`, `meta/llama-3.1-405b-instruct`, `mistralai/mixtral-8x7b-instruct-v0.1`
- Requires `NVIDIA_API_KEY` environment variable
- Supports tool calling with `parallel_tool_calls=false`

### MCP (Model Context Protocol)
- Configured via `mcp_config.json` with `mcpServers` object
- Each server: `command`, `args`, optional `env`
- Tools namespaced as `mcp_{server}__{tool}`
- Use `stdio_client` for local servers

### Streamlit
- Version 1.35+ (current: 1.59.2)
- Use `st.session_state` for persistence
- `st.chat_message` for chat UI
- `st.experimental_fragment` for async loops (1.32+)
- Avoid `asyncio.run()` in callbacks

---

## Common Mistakes

| Mistake | How to Avoid |
|---------|--------------|
| Using root/agent.py instead of src/agent | Import from `src.chatbot.application.agent` |
| Forgetting MCP cleanup on Streamlit rerun | Use async context manager or `atexit` |
| Hardcoding model names | Use `Settings.nim_model` |
| Storing API keys in session_state | Use `st.secrets` in production |
| Not validating MCP server args | Validate against allowlist in config loader |
| Blocking UI with long agent runs | Use fragments or background execution |

---

## Procedures

### Adding a New Tool Provider
1. Create class implementing `ToolProvider` protocol in `infrastructure/`
2. Add `get_tools()` returning `List[ToolDefinition]`
3. Add `call_tool(name, arguments)` method
4. Register in `NIMAgent.__init__` via `ToolExecutor`

### Adding a New LLM Provider
1. Create class implementing `LLMProvider` protocol in `infrastructure/llm/`
2. Implement `chat_completion()` and `close()`
3. Update `AgentConfig` to accept provider type
4. Factory function to create provider from config

### Running the App
```bash
# Development
streamlit run app.py

# With src/ architecture (after migration)
streamlit run src/chatbot/app.py  # or create new entry point
```

### Testing MCP Servers
```bash
# Test connection
python -c "
import asyncio
from src.chatbot.infrastructure.mcp.client import MCPClientManager
async def test():
    m = MCPClientManager()
    await m.connect_all()
    print(m.get_connected_servers())
asyncio.run(test())
"
```

---

## Security Checklist

- [ ] All file paths validated against workspace root (no symlink following)
- [ ] Command allowlist excludes interpreters (python, node, bash, npx)
- [ ] No `unsafe_allow_html` with user content
- [ ] API keys from `st.secrets` not session_state
- [ ] MCP config validated on load
- [ ] Chat history encrypted or access-controlled
- [ ] Resource cleanup on session end

---

## Migration Checklist (Root → src/)

- [ ] Delete root `agent.py`, `tools.py`, `mcp_client.py`
- [ ] Update `app.py` to import from `src.chatbot.application.agent`
- [ ] Update `app.py` to use `src.chatbot.domain.workspace.Workspace`
- [ ] Update `app.py` to use `src.chatbot.infrastructure.mcp.client.MCPClientManager`
- [ ] Update `app.py` to use `src.chatbot.config.get_settings()`
- [ ] Remove duplicate `LOCAL_TOOLS` definitions
- [ ] Consolidate `AgentConfig` to single definition
- [ ] Update `requirements.txt` if needed
- [ ] Test full chat flow with MCP tools
- [ ] Test file operations, command execution
- [ ] Verify session persistence works

---

## Future Extensions (General Agent Capabilities)

The `src/` architecture supports these via new providers:

| Capability | Provider Type | Implementation |
|------------|--------------|----------------|
| Web search | `ToolProvider` | `WebSearchProvider` using SerpAPI/Brave |
| Resume builder | `LLMProvider` + `ToolProvider` | Template-based with LLM customization |
| Game mod installer | `ToolProvider` | Game-specific file operations |
| Code analysis | `ToolProvider` | AST-based analysis tools |
| API testing | `ToolProvider` | HTTP client with OpenAPI parsing |

Each capability adds a new provider without modifying core agent logic.

---

## Commands Reference

| Command | Purpose |
|---------|---------|
| `streamlit run app.py` | Run legacy app |
| `python -m pytest` | Run tests (when added) |
| `ruff check .` | Lint code |
| `black .` | Format code |
| `mypy src/` | Type check |
| `/evolve` | Update runbooks from learnings |

---

## Related Documents

- [src/chatbot/README.md] - Architecture overview
- [mcp_config.json] - MCP server configuration
- [.env.example] - Environment variables template
- [CLAUDE.md] - User instructions