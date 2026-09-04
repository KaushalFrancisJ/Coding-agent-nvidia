# NVIDIA NIM Coding Agent Runbook

Last updated: 2026-07-26

## Status: Migration in Progress

The Streamlit UI is **deprecated**. All backend architecture, security fixes, async tools, MCP integration, and streaming infrastructure are complete and stable. The final migration step is replacing the Streamlit UI layer with **Flet** — a compiled desktop application framework for Python.

---

## What's Already Done (Stable — Do Not Revisit)

### Backend Architecture
- **Clean Architecture**: `src/chatbot/` with domain/application/infrastructure layers
- **Protocol-based providers**: `LLMProvider`, `ToolProvider`, `SessionStore`
- **Async file operations**: `aiofiles` for I/O, `asyncio.create_subprocess_exec` for commands
- **Workspace sandboxing**: Path traversal prevention, symlink attack protection
- **MCP Client**: `AsyncExitStack` lifecycle, tool prefixing (`mcp_server__tool`), connect/cleanup

### Security (Verified)
- Path traversal blocked (absolute paths, `..` traversal, symlink escapes)
- Command injection prevented (no interpreters in allowlist)
- API keys loaded from secrets/environment only (never user input)
- No `unsafe_allow_html` with user content
- MCP config validated on load (command allowlist, arg sanitization)

### LLM & Agent
- `NIMLLMProvider`: Non-streaming + `chat_completion_stream()` async generator
- `NIMAgent.run_stream()`: Streaming agent loop with tool call execution
- Config via `Settings` (pydantic-settings, `.env` file)
- Session persistence (`chats/*.json`)

### Testing
- `test_security.py`: 9 tests covering path traversal, MCP validation, command blocking
- `python -m py_compile *.py`: All files compile clean

---

## Current UI (Streamlit — to be replaced)

### What Works in app.py
- Chat input/output with `st.chat_message`
- Interrupt/Stop button via `st.session_state.processing` flag
- Background thread + queue for streaming tokens
- `st.write_stream()` for real-time token display
- Chat history sidebar with session management
- Model selector, MCP server connection UI

### What's Broken (Why We're Migrating)
1. **`asyncio.run()` blocks the script run** — Streamlit only flushes WebSocket deltas to the browser *after* the script finishes, so streaming stutters or seems to hang
2. **Threading hacks needed** — background thread + queue + generator just to get real-time tokens, and it still fights Streamlit's execution model
3. **No true compiled binary** — requires `streamlit run`; not a standalone `.exe`
4. **WebSocket latency** — every update round-trips through the browser

---

## Target: Flet Desktop Application

Flet runs Python code as a native desktop window (Flutter engine under the hood). Async event loop runs natively — `page.update()` flushes immediately without blocking.

### Architecture

```
main.py (Flet UI — native desktop window)
    ├── page (Flet Page — main window)
    │   ├── ChatView (ListView of messages)
    │   ├── TextField (chat input)
    │   └── Row (Send + Stop buttons)
    └── src/chatbot/ (UNCHANGED — same code you have now)
```

### Comparison

| Capability | Streamlit (Current) | Flet (Target) |
|------------|-------------------|---------------|
| Deployment | `streamlit run app.py` | `flet pack main.py` → `dist/main.exe` |
| Binary size | N/A (server) | ~50 MB |
| Streaming | WebSocket, flush-after-script | Native `page.update()` immediate |
| Async loop | Blocked by script run | Native event loop |
| Threading needed | Yes (queue + thread) | No (just `async/await`) |
| UI components | Server-rendered React | Native Material Design (Flutter) |

### Quick Migration: `main.py` (~200 lines)

```python
import flet as ft
import asyncio
from src.chatbot.application.agent import NIMAgent, AgentConfig, AgentEvent
from src.chatbot.domain.models import Message, Role
from src.chatbot.config import get_settings

def main(page: ft.Page):
    page.title = "NIM Coding Agent"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 0

    chat = ft.ListView(expand=True, spacing=10, padding=20)

    async def send_message(e):
        if not input_field.value.strip():
            return
        
        # Add user message
        chat.controls.append(ft.Row(
            [ft.Container(ft.Text(input_field.value), bgcolor=ft.colors.BLUE_900, padding=10, border_radius=10)],
            alignment=ft.MainAxisAlignment.END
        ))
        input_field.value = ""
        page.update()

        # Streaming response
        config = AgentConfig(api_key="your-key", model="meta/llama-3.1-70b-instruct")
        msg = ft.Column([])
        chat.controls.append(msg)
        response_text = ""

        async def on_token(event: AgentEvent):
            nonlocal response_text
            if event.role == "assistant" and event.content:
                response_text += event.content
                msg.controls.clear()
                msg.controls.append(ft.Text(response_text))  # updates in real-time
                page.update()

        async with NIMAgent(config) as agent:
            result = await agent.run_stream(
                messages=[Message.user(input_field.value)],
                yield_callback=on_token
            )

    input_field = ft.TextField(hint_text="Ask something...", expand=True)
    send_btn = ft.IconButton(icon=ft.icons.SEND_ROUNDED, on_click=send_message)

    page.add(
        ft.Container(chat, expand=True),
        ft.Container(
            ft.Row([input_field, send_btn]),
            padding=10,
            bgcolor=ft.colors.SURFACE_VARIANT
        )
    )

ft.app(target=main)  # Runs as native window
```

### Compilation

```bash
# Install
pip install flet

# Run
python main.py          # Development — native window opens immediately

# Compile to standalone binary
flet pack main.py        # → dist/main.exe (Windows)
                         # → dist/main     (Linux)
                         # → dist/main.app (macOS)
```

The compiled binary is fully self-contained — no Python, no npm, no server required.

### Migration Steps

1. Create `main.py` with Flet UI code (~200 lines)
2. Keep `src/chatbot/` completely unchanged (all agent/MCP/security logic)
3. Delete `app.py`, `test_security.py`, `.streamlit/` (cleanup)
4. Update `requirements.txt`: remove `streamlit`, add `flet`
5. Run `flet pack main.py` for production binary

---

## Future: General Purpose Agent

After the Flet migration, the architecture is ready for extensions:

| Capability | How to Add |
|------------|------------|
| Web search | New `ToolProvider` using SerpAPI/Brave |
| Resume builder | Chain LLM calls with templated prompts |
| Game mod installer | `ToolProvider` for archive extraction + game dir operations |
| Multi-step tasks | Use sequential-thinking MCP for task decomposition |
| Persistent memory | Implement `SessionStore` with SQLite/vector DB |
| API testing | `ToolProvider` wrapping HTTP client |

Each capability adds a provider without modifying the core agent — that's what the clean architecture was built for.

---

## Related Files

- `src/chatbot/` — Complete backend (untouched during migration)
- `mcp_config.json` — MCP server configuration (context7, sequential-thinking)
- `.env.example` — Environment variables template
- `.claude/skills/runbooks/SKILL.md` — Runbook system documentation