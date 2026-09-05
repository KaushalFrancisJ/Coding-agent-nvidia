"""
NVIDIA NIM Coding Agent — Flet Desktop UI
Replaces Streamlit with a native desktop window via Flet (Flutter engine).
Backend src/chatbot/ is completely unchanged.
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

import flet as ft
from dotenv import load_dotenv

from src.chatbot.application.agent import AgentConfig, AgentEvent, NIMAgent
from src.chatbot.config import get_settings
from src.chatbot.domain.models import Message, Role
from src.chatbot.infrastructure.mcp.client import MCPClientManager

load_dotenv()

CHATS_DIR = "chats"
os.makedirs(CHATS_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Palette helpers
# ---------------------------------------------------------------------------
_USER_BG = ft.Colors.BLUE_900
_ASSISTANT_BG = ft.Colors.GREY_800
_TOOL_BG = ft.Colors.AMBER_900
_SIDEBAR_BG = ft.Colors.GREY_900
_INPUT_BAR_BG = ft.Colors.GREY_800


# ---------------------------------------------------------------------------
# Persistence helpers (same logic as app.py)
# ---------------------------------------------------------------------------

def save_chat(session_id: str, messages: list[dict], timestamp: str) -> None:
    if not messages:
        return
    file_path = os.path.join(CHATS_DIR, f"{session_id}.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump({"id": session_id, "timestamp": timestamp, "messages": messages}, f, indent=2)


def load_chat_data(session_id: str) -> dict | None:
    file_path = os.path.join(CHATS_DIR, f"{session_id}.json")
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def get_chat_history() -> list[dict]:
    chats: list[dict] = []
    for f in glob.glob(os.path.join(CHATS_DIR, "*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            title = "New Chat"
            for msg in data.get("messages", []):
                if msg["role"] == "user":
                    raw = msg["content"] or ""
                    title = raw[:40] + ("..." if len(raw) > 40 else "")
                    break
            chats.append({"id": data["id"], "timestamp": data.get("timestamp", ""), "title": title})
        except Exception:
            pass
    chats.sort(key=lambda x: x["timestamp"], reverse=True)
    return chats


def get_api_key() -> str:
    key = os.environ.get("NVIDIA_API_KEY", "")
    return key


def get_env_model() -> str:
    return os.environ.get("MODEL", "meta/llama-3.1-70b-instruct")


MODEL_OPTIONS = [
    "meta/llama-3.1-70b-instruct",
    "meta/llama-3.1-405b-instruct",
    "mistralai/mixtral-8x7b-instruct-v0.1",
]


# ---------------------------------------------------------------------------
# Control builders
# ---------------------------------------------------------------------------

def _user_bubble(text: str) -> ft.Control:
    return ft.Row(
        [
            ft.Container(
                ft.Text(text, selectable=True, color=ft.Colors.WHITE),
                bgcolor=_USER_BG,
                padding=ft.Padding(left=14, right=14, top=10, bottom=10),
                border_radius=12,
            )
        ],
        alignment=ft.MainAxisAlignment.END,
    )


def _assistant_bubble(markdown_ref: ft.Ref[ft.Markdown]) -> ft.Control:
    md = ft.Markdown(
        value="▌",
        extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
        code_theme="atom-one-dark",
        selectable=True,
        expand=True,
        ref=markdown_ref,
        on_tap_link=lambda e: None,  # suppress link-tap warnings
    )
    return ft.Row(
        [
            ft.Container(
                ft.Row([ft.Icon(ft.Icons.SMART_TOY_OUTLINED, size=18, color=ft.Colors.BLUE_200), md]),
                bgcolor=_ASSISTANT_BG,
                padding=ft.Padding(left=14, right=14, top=10, bottom=10),
                border_radius=12,
                expand=True,
            )
        ],
        alignment=ft.MainAxisAlignment.START,
        expand=True,
    )


def _tool_bubble(name: str, arguments: dict | None) -> ft.Control:
    args_text = json.dumps(arguments, indent=2) if arguments else ""
    controls: list[ft.Control] = [
        ft.Row([
            ft.Icon(ft.Icons.BUILD_OUTLINED, size=16, color=ft.Colors.AMBER_200),
            ft.Text(f"Executing: {name}", weight=ft.FontWeight.BOLD,
                    color=ft.Colors.AMBER_200, size=13),
        ])
    ]
    if args_text:
        controls.append(
            ft.Text(args_text, style=ft.TextStyle(style=ft.TextStyle(font_family="monospace")), size=11,
                    color=ft.Colors.GREY_300, selectable=True)
        )
    return ft.Container(
        ft.Column(controls, spacing=4),
        bgcolor=_TOOL_BG,
        padding=ft.Padding(left=14, right=14, top=8, bottom=8),
        border_radius=8,
        margin=ft.Margin(left=4),
    )


def _message_to_control(msg: dict) -> ft.Control | None:
    """Rebuild a persisted message dict as a Flet control (no streaming ref)."""
    role = msg.get("role")
    if role == "user":
        return _user_bubble(msg.get("content") or "")
    elif role == "assistant" and msg.get("content"):
        ref = ft.Ref[ft.Markdown]()
        ctrl = _assistant_bubble(ref)
        if ref.current:
            ref.current.value = msg["content"]
        else:
            # Directly patch the markdown inside the Row → Container → Row → Markdown
            _patch_markdown(ctrl, msg["content"])
        return ctrl
    elif role == "tool_executing":
        return _tool_bubble(msg.get("name", ""), msg.get("arguments"))
    return None


def _patch_markdown(row_ctrl: ft.Control, value: str) -> None:
    """Reach into the nested control tree to update Markdown value."""
    try:
        # Row → Container → Row → [Icon, Markdown]
        md = row_ctrl.controls[0].content.controls[1]
        md.value = value
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main Flet App
# ---------------------------------------------------------------------------

async def main(page: ft.Page) -> None:
    page.title = "NVIDIA NIM Coding Agent"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 0
    page.bgcolor = ft.Colors.GREY_900
    page.window.width = 1200
    page.window.height = 800
    page.window.min_width = 700
    page.window.min_height = 500
    page.fonts = {"monospace": "Courier New"}

    settings = get_settings()

    # -----------------------------------------------------------------------
    # Mutable application state
    # -----------------------------------------------------------------------
    state: dict = {
        "session_id": str(uuid.uuid4()),
        "messages": [],
        "timestamp": datetime.now().isoformat(),
        "processing": False,
        "agent_task": None,
    }

    mcp_manager = MCPClientManager(str(settings.mcp_config_path))

    # -----------------------------------------------------------------------
    # Chat view (main message list)
    # -----------------------------------------------------------------------
    chat_view = ft.ListView(
        expand=True,
        spacing=10,
        padding=ft.Padding(left=20, right=20, top=16, bottom=16),
        auto_scroll=True,
    )

    # -----------------------------------------------------------------------
    # Welcome banner (shown when chat is empty)
    # -----------------------------------------------------------------------
    welcome_banner = ft.Container(
        ft.Column(
            [
                ft.Icon(ft.Icons.SMART_TOY, size=56, color=ft.Colors.BLUE_400),
                ft.Text(
                    "Welcome to NIM Coding Agent",
                    size=22,
                    weight=ft.FontWeight.BOLD,
                    color=ft.Colors.WHITE,
                ),
                ft.Text(
                    "Ask me to write code, explore files, run commands,\n"
                    "or help with any development task.",
                    text_align=ft.TextAlign.CENTER,
                    color=ft.Colors.GREY_400,
                    size=14,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=12,
        ),
        alignment=ft.Alignment.CENTER,
        expand=True,
        visible=True,
    )

    # -----------------------------------------------------------------------
    # Input bar
    # -----------------------------------------------------------------------
    input_field = ft.TextField(
        hint_text="Ask me to edit code, run a task, or explore files...",
        expand=True,
        multiline=True,
        max_lines=6,
        min_lines=1,
        shift_enter=True,
        border_radius=10,
        filled=True,
        bgcolor=ft.Colors.GREY_800,
        border_color=ft.Colors.GREY_600,
        focused_border_color=ft.Colors.BLUE_400,
        text_size=14,
        on_submit=lambda e: asyncio.create_task(_send_message()),
    )

    send_btn = ft.IconButton(
        icon=ft.Icons.SEND_ROUNDED,
        icon_color=ft.Colors.BLUE_400,
        icon_size=24,
        tooltip="Send (Enter)",
        on_click=lambda e: asyncio.create_task(_send_message()),
    )

    stop_btn = ft.Button(
        "⏹  Stop Generation",
        icon=ft.Icons.STOP_CIRCLE_OUTLINED,
        bgcolor=ft.Colors.RED_900,
        color=ft.Colors.WHITE,
        visible=False,
        on_click=lambda e: asyncio.create_task(_stop_generation()),
    )

    # -----------------------------------------------------------------------
    # Sidebar — chat history
    # -----------------------------------------------------------------------
    chat_list_col = ft.Column(scroll=ft.ScrollMode.AUTO, spacing=4, expand=True)

    # -----------------------------------------------------------------------
    # Sidebar — configuration
    # -----------------------------------------------------------------------
    def _save_api_key(e):
        import os
        val = e.control.value.strip()
        os.environ["NVIDIA_API_KEY"] = val
        settings.nvidia_api_key = val
        
        env_path = ".env"
        lines = []
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as env_file:
                lines = env_file.readlines()
        
        found = False
        for i in range(len(lines)):
            if lines[i].startswith("NVIDIA_API_KEY="):
                lines[i] = f"NVIDIA_API_KEY={val}\\n"
                found = True
                break
        if not found:
            lines.append(f"\\nNVIDIA_API_KEY={val}\\n")
            
        with open(env_path, "w", encoding="utf-8") as env_file:
            env_file.writelines(lines)

        e.control.border_color = ft.Colors.GREEN_400 if val else ft.Colors.RED_400
        e.page.update()

    api_key_field = ft.TextField(
        label="NVIDIA_API_KEY",
        value=settings.nvidia_api_key or get_api_key(),
        password=True,
        can_reveal_password=True,
        text_size=12,
        height=40,
        content_padding=10,
        on_change=_save_api_key,
        border_color=ft.Colors.GREEN_400 if (settings.nvidia_api_key or get_api_key()) else ft.Colors.RED_400,
    )
    api_key_status = ft.Container(api_key_field, padding=ft.Padding(bottom=8, left=0, right=0, top=0))

    env_model = get_env_model()
    _model_opts = list(MODEL_OPTIONS)
    if env_model not in _model_opts:
        _model_opts.insert(0, env_model)

    def _add_model(e):
        def close_dlg(e2):
            dlg.open = False
            page.update()
        def save_dlg(e2):
            m = new_m_field.value.strip()
            if m and m not in _model_opts:
                _model_opts.append(m)
                model_dropdown.options.append(ft.dropdown.Option(m))
                model_dropdown.value = m
            dlg.open = False
            page.update()
        
        new_m_field = ft.TextField(label="Model ID")
        dlg = ft.AlertDialog(
            title=ft.Text("Add Model"),
            content=new_m_field,
            actions=[
                ft.TextButton("Cancel", on_click=close_dlg),
                ft.TextButton("Add", on_click=save_dlg),
            ]
        )
        page.overlay.append(dlg)
        dlg.open = True
        page.update()

    def _remove_model(e):
        current = model_dropdown.value
        if current and current in _model_opts:
            _model_opts.remove(current)
            model_dropdown.options = [ft.dropdown.Option(m) for m in _model_opts]
            model_dropdown.value = _model_opts[0] if _model_opts else None
            page.update()

    model_dropdown = ft.Dropdown(
        label="Model",
        value=env_model,
        options=[ft.dropdown.Option(m) for m in _model_opts],
        dense=True,
        text_size=12,
        label_style=ft.TextStyle(size=12, color=ft.Colors.GREY_400),
        bgcolor=ft.Colors.GREY_800,
        border_radius=8,
        expand=True,
    )
    
    model_row = ft.Row(
        [
            model_dropdown, 
            ft.IconButton(ft.Icons.ADD, on_click=_add_model, tooltip="Add custom model"),
            ft.IconButton(ft.Icons.DELETE, on_click=_remove_model, tooltip="Remove selected model", icon_color=ft.Colors.RED_400)
        ], 
        spacing=4
    )

    config_tile = ft.ExpansionTile(
        title=ft.Text("⚙️  Configuration", size=13),
        controls=[
            ft.Container(
                ft.Column([api_key_status, model_row], spacing=8),
                padding=ft.Padding(left=8, right=8, bottom=8),
            )
        ],
        expanded=False,
        tile_padding=ft.Padding(left=8, right=8, top=0, bottom=0),
    )

    # -----------------------------------------------------------------------
    # Sidebar — MCP servers
    # -----------------------------------------------------------------------
    mcp_status_col = ft.Column(spacing=4)

    def _refresh_mcp_status() -> None:
        mcp_status_col.controls.clear()
        connected = mcp_manager.get_connected_servers()
        if connected:
            for srv_name, count in connected.items():
                mcp_status_col.controls.append(
                    ft.Row([
                        ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREEN_400, size=14),
                        ft.Text(f"{srv_name} ({count} tools)", size=12, color=ft.Colors.GREEN_300),
                    ], spacing=4)
                )
        else:
            mcp_status_col.controls.append(
                ft.Text("No servers connected.", size=12, color=ft.Colors.GREY_500)
            )

    _refresh_mcp_status()

    def _edit_mcp_config(e):
        def close_dlg(e2):
            dlg.open = False
            page.update()
        def save_dlg(e2):
            try:
                content = editor.value
                import json
                json.loads(content) # validate JSON
                with open(settings.mcp_config_path, "w", encoding="utf-8") as config_file:
                    config_file.write(content)
                dlg.open = False
                page.update()
            except Exception as exc:
                page.overlay.append(ft.SnackBar(ft.Text(f"Invalid JSON: {exc}"), bgcolor=ft.Colors.RED_900))
                page.overlay[-1].open = True
                page.update()
        
        try:
            with open(settings.mcp_config_path, "r", encoding="utf-8") as config_file:
                current_config = config_file.read()
        except:
            current_config = "{}"

        editor = ft.TextField(
            value=current_config,
            multiline=True,
            min_lines=10,
            max_lines=20,
            text_size=12,
            style=ft.TextStyle(font_family="monospace")
        )
        dlg = ft.AlertDialog(
            title=ft.Text("Edit MCP Config"),
            content=editor,
            actions=[
                ft.TextButton("Cancel", on_click=close_dlg),
                ft.TextButton("Save", on_click=save_dlg),
            ]
        )
        page.overlay.append(dlg)
        dlg.open = True
        page.update()

    connect_mcp_btn = ft.Button(
        "🔌 Connect",
        icon=ft.Icons.ELECTRICAL_SERVICES,
        on_click=lambda e: page.run_task(_connect_mcp),
        style=ft.ButtonStyle(bgcolor=ft.Colors.BLUE_900),
        expand=True,
    )
    edit_mcp_btn = ft.Button(
        "Edit Config",
        icon=ft.Icons.EDIT,
        on_click=_edit_mcp_config,
    )
    mcp_buttons_row = ft.Row([connect_mcp_btn, edit_mcp_btn], spacing=4)

    mcp_tile = ft.ExpansionTile(
        title=ft.Text("🔌  MCP Servers", size=13),
        controls=[
            ft.Container(
                ft.Column([mcp_buttons_row, mcp_status_col], spacing=8),
                padding=ft.Padding(left=8, right=8, bottom=8),
            )
        ],
        expanded=False,
        tile_padding=ft.Padding(left=8, right=8, top=0, bottom=0),
    )

    # -----------------------------------------------------------------------
    # Sidebar layout
    # -----------------------------------------------------------------------
    new_chat_btn = ft.Button(
        "➕  New Chat",
        icon=ft.Icons.ADD,
        expand=True,
        on_click=lambda e: asyncio.create_task(_new_chat()),
    )

    sidebar = ft.Container(
        ft.Column(
            [
                ft.Container(
                    ft.Text("💬  Chats", size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                    padding=ft.Padding(top=16, left=12, bottom=8),
                ),
                ft.Container(
                    ft.Row([new_chat_btn]),
                    padding=ft.Padding(left=8, right=8, top=0, bottom=0),
                ),
                ft.Divider(height=12, color=ft.Colors.GREY_700),
                ft.Container(chat_list_col, expand=True, padding=ft.Padding(left=4, right=4, top=0, bottom=0)),
                ft.Divider(height=12, color=ft.Colors.GREY_700),
                config_tile,
                mcp_tile,
                ft.Container(height=8),
            ],
            spacing=0,
            expand=True,
        ),
        width=280,
        bgcolor=_SIDEBAR_BG,
        expand=False,
    )

    # -----------------------------------------------------------------------
    # Main content layout
    # -----------------------------------------------------------------------
    header = ft.Container(
        ft.Row(
            [
                ft.Icon(ft.Icons.SMART_TOY, color=ft.Colors.BLUE_400, size=26),
                ft.Text(
                    "NVIDIA NIM Coding Agent",
                    size=18,
                    weight=ft.FontWeight.BOLD,
                    color=ft.Colors.WHITE,
                ),
                ft.Container(
                    ft.Text("Powered by NIM, MCP & Flet", size=11, color=ft.Colors.GREY_500),
                    margin=ft.Margin(left=8),
                ),
            ],
            spacing=8,
        ),
        padding=ft.Padding(left=20, right=20, top=10, bottom=10),
        bgcolor=ft.Colors.GREY_800,
        border=ft.Border(bottom=ft.BorderSide(1, ft.Colors.GREY_700)),
    )

    chat_area = ft.Stack(
        [
            welcome_banner,
            chat_view,
        ],
        expand=True,
    )

    input_bar = ft.Container(
        ft.Column(
            [
                ft.Row([stop_btn], alignment=ft.MainAxisAlignment.CENTER),
                ft.Row([input_field, send_btn], vertical_alignment=ft.CrossAxisAlignment.END),
            ],
            spacing=6,
        ),
        padding=ft.Padding(left=16, right=16, top=10, bottom=10),
        bgcolor=_INPUT_BAR_BG,
        border=ft.Border(top=ft.BorderSide(1, ft.Colors.GREY_700)),
    )

    main_content = ft.Column(
        [header, chat_area, input_bar],
        spacing=0,
        expand=True,
    )

    page.add(
        ft.Row(
            [sidebar, ft.VerticalDivider(width=1, color=ft.Colors.GREY_700), main_content],
            spacing=0,
            expand=True,
        )
    )

    # -----------------------------------------------------------------------
    # Helper: refresh chat list in sidebar
    # -----------------------------------------------------------------------
    def _refresh_chat_list() -> None:
        chat_list_col.controls.clear()
        for chat in get_chat_history():
            is_active = chat["id"] == state["session_id"]
            btn = ft.TextButton(("▸  " if is_active else "    ") + chat["title"],
                data=chat["id"],
                style=ft.ButtonStyle(
                    color=ft.Colors.WHITE if is_active else ft.Colors.GREY_400,
                    bgcolor={
                        ft.ControlState.DEFAULT: ft.Colors.BLUE_900 if is_active else ft.Colors.TRANSPARENT,
                        ft.ControlState.HOVERED: ft.Colors.GREY_800,
                    },
                    padding=ft.Padding(left=8, right=8, top=4, bottom=4),
                ),
                expand=True,
                on_click=lambda e: asyncio.create_task(_load_chat(e.control.data)),
            )
            chat_list_col.controls.append(btn)

    # -----------------------------------------------------------------------
    # Helper: rebuild chat_view from state["messages"]
    # -----------------------------------------------------------------------
    def _save_api_key(e):
        import os
        val = e.control.value.strip()
        os.environ["NVIDIA_API_KEY"] = val
        settings.nvidia_api_key = val

    def _rebuild_chat_view() -> None:
        chat_view.controls.clear()
        has_content = False
        for msg in state["messages"]:
            ctrl = _message_to_control(msg)
            if ctrl:
                chat_view.controls.append(ctrl)
                has_content = True
        welcome_banner.visible = not has_content

    # -----------------------------------------------------------------------
    # Helper: set processing state (enable/disable inputs)
    # -----------------------------------------------------------------------
    def _set_processing(value: bool) -> None:
        state["processing"] = value
        input_field.disabled = value
        send_btn.disabled = value
        new_chat_btn.disabled = value
        stop_btn.visible = value

    # -----------------------------------------------------------------------
    # Event: send message
    # -----------------------------------------------------------------------
    async def _send_message() -> None:
        if state["processing"]:
            return
        text = (input_field.value or "").strip()
        if not text:
            return

        api_key = get_api_key()
        if not api_key:
            page.open(ft.SnackBar(
                ft.Text("⚠️  NVIDIA_API_KEY not configured. Set it in your .env file."),
                bgcolor=ft.Colors.ORANGE_900,
            ))
            page.update()
            return

        # Append user message to state and UI
        state["messages"].append({"role": "user", "content": text})
        chat_view.controls.append(_user_bubble(text))
        welcome_banner.visible = False
        input_field.value = ""
        _set_processing(True)
        page.update()

        # Build a Markdown control for streaming assistant response
        md_ref = ft.Ref[ft.Markdown]()
        assistant_row = _assistant_bubble(md_ref)
        chat_view.controls.append(assistant_row)
        page.update()

        # Build agent messages from history
        agent_messages = _build_agent_messages()

        model = model_dropdown.value or get_env_model()
        config = AgentConfig(api_key=api_key, model=model)

        # Track accumulated text for the current assistant turn
        response_state = {"text": "", "md_ctrl": md_ref.current}

        async def on_event(event: AgentEvent) -> None:
            if event.role == "assistant" and event.content:
                response_state["text"] += event.content
                if response_state["md_ctrl"]:
                    response_state["md_ctrl"].value = response_state["text"] + " ▌"
                page.update()

            elif event.role == "tool_executing":
                # Add a tool bubble *before* the streaming assistant continues
                tool_ctrl = _tool_bubble(event.name or "", event.arguments)
                chat_view.controls.append(tool_ctrl)
                state["messages"].append({
                    "role": "tool_executing",
                    "name": event.name,
                    "arguments": event.arguments,
                })
                page.update()

            elif event.role == "error":
                page.open(ft.SnackBar(
                    ft.Text(f"Agent error: {event.error}"),
                    bgcolor=ft.Colors.RED_900,
                ))
                page.update()

        async def run_agent() -> None:
            try:
                agent = NIMAgent(config, mcp_manager=mcp_manager)
                await agent.initialize()
                try:
                    final_messages = await agent.run_stream(agent_messages, on_event)
                finally:
                    await agent.close()

                # Strip streaming cursor from final text
                if response_state["md_ctrl"] and response_state["text"]:
                    response_state["md_ctrl"].value = response_state["text"]

                # Sync state["messages"] with the canonical final_messages from agent
                _sync_messages_from_agent(final_messages)
                save_chat(state["session_id"], state["messages"], state["timestamp"])

            except asyncio.CancelledError:
                # User hit Stop — finalize whatever was accumulated
                if response_state["md_ctrl"] and response_state["text"]:
                    response_state["md_ctrl"].value = response_state["text"] + "\n\n*(stopped)*"
                save_chat(state["session_id"], state["messages"], state["timestamp"])

            except Exception as exc:
                page.overlay.append(ft.SnackBar(ft.Text(f"Agent error: {exc}", color=ft.Colors.WHITE), bgcolor=ft.Colors.RED_900, open=True))

            finally:
                _set_processing(False)
                state["agent_task"] = None
                _refresh_chat_list()
                page.update()

        task = asyncio.create_task(run_agent())
        state["agent_task"] = task
        _refresh_chat_list()
        page.update()

    # -----------------------------------------------------------------------
    # Event: stop generation
    # -----------------------------------------------------------------------
    async def _stop_generation() -> None:
        task = state.get("agent_task")
        if task and not task.done():
            task.cancel()

    # -----------------------------------------------------------------------
    # Event: new chat
    # -----------------------------------------------------------------------
    async def _new_chat() -> None:
        if state["processing"]:
            return
        state["session_id"] = str(uuid.uuid4())
        state["messages"] = []
        state["timestamp"] = datetime.now().isoformat()
        chat_view.controls.clear()
        welcome_banner.visible = True
        _refresh_chat_list()
        page.update()

    # -----------------------------------------------------------------------
    # Event: load existing chat
    # -----------------------------------------------------------------------
    async def _load_chat(session_id: str) -> None:
        if state["processing"]:
            return
        data = load_chat_data(session_id)
        if data:
            state["session_id"] = data["id"]
            state["messages"] = data["messages"]
            state["timestamp"] = data.get("timestamp", datetime.now().isoformat())
            _rebuild_chat_view()
            _refresh_chat_list()
            page.update()

    # -----------------------------------------------------------------------
    # Event: connect MCP servers
    # -----------------------------------------------------------------------
    async def _connect_mcp(e=None) -> None:
        connect_mcp_btn.disabled = True
        connect_mcp_btn.text = "Connecting..."
        page.update()
        try:
            await mcp_manager.connect_all()
            _refresh_mcp_status()
        except Exception as exc:
            page.overlay.append(ft.SnackBar(ft.Text(f"MCP connection error: {exc}", color=ft.Colors.WHITE), bgcolor=ft.Colors.RED_900, open=True))
        finally:
            connect_mcp_btn.disabled = False
            connect_mcp_btn.text = "🔌  Connect MCP Servers"
            page.update()

    # -----------------------------------------------------------------------
    # Helper: convert state messages to agent Message objects
    # -----------------------------------------------------------------------
    def _build_agent_messages() -> list[Message]:
        result = []
        for m in state["messages"]:
            role = m.get("role")
            if role == "user":
                result.append(Message.user(m["content"]))
            elif role == "assistant" and m.get("content"):
                msg = Message.assistant(content=m["content"])
                if m.get("tool_calls"):
                    msg.tool_calls = m["tool_calls"]
                result.append(msg)
            elif role == "tool":
                result.append(Message.tool_result(
                    tool_call_id=m.get("tool_call_id", ""),
                    name=m.get("name", ""),
                    content=m.get("content", ""),
                ))
        return result

    # -----------------------------------------------------------------------
    # Helper: sync state messages from agent's canonical message list
    # -----------------------------------------------------------------------
    def _sync_messages_from_agent(final_messages: list[Message]) -> None:
        new_msgs: list[dict] = []
        for m in final_messages:
            if m.role == Role.SYSTEM:
                continue
            elif m.role == Role.USER:
                new_msgs.append({"role": "user", "content": m.content})
            elif m.role == Role.ASSISTANT:
                entry: dict = {"role": "assistant", "content": m.content}
                if m.tool_calls:
                    entry["tool_calls"] = m.tool_calls
                new_msgs.append(entry)
            elif m.role == Role.TOOL:
                new_msgs.append({
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "name": m.name,
                    "content": m.content,
                })
        state["messages"] = new_msgs

    # -----------------------------------------------------------------------
    # Initial render
    # -----------------------------------------------------------------------
    _refresh_chat_list()
    page.update()
    page.run_task(_connect_mcp)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    ft.run(main)
