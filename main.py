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
_ASSISTANT_BG = ft.Colors.GREY_850
_TOOL_BG = ft.Colors.AMBER_900
_SIDEBAR_BG = ft.Colors.GREY_900
_INPUT_BAR_BG = ft.Colors.GREY_850


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
                padding=ft.padding.symmetric(horizontal=14, vertical=10),
                border_radius=12,
                max_width=640,
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
                padding=ft.padding.symmetric(horizontal=14, vertical=10),
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
            ft.Text(args_text, font_family="monospace", size=11,
                    color=ft.Colors.GREY_300, selectable=True)
        )
    return ft.Container(
        ft.Column(controls, spacing=4),
        bgcolor=_TOOL_BG,
        padding=ft.padding.symmetric(horizontal=14, vertical=8),
        border_radius=8,
        margin=ft.margin.only(left=4),
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

def main(page: ft.Page) -> None:
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
        padding=ft.padding.symmetric(horizontal=20, vertical=16),
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
        alignment=ft.alignment.center,
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
        on_submit=lambda e: asyncio.ensure_future(_send_message()),
    )

    send_btn = ft.IconButton(
        icon=ft.Icons.SEND_ROUNDED,
        icon_color=ft.Colors.BLUE_400,
        icon_size=24,
        tooltip="Send (Enter)",
        on_click=lambda e: asyncio.ensure_future(_send_message()),
    )

    stop_btn = ft.ElevatedButton(
        "⏹  Stop Generation",
        icon=ft.Icons.STOP_CIRCLE_OUTLINED,
        bgcolor=ft.Colors.RED_900,
        color=ft.Colors.WHITE,
        visible=False,
        on_click=lambda e: asyncio.ensure_future(_stop_generation()),
    )

    # -----------------------------------------------------------------------
    # Sidebar — chat history
    # -----------------------------------------------------------------------
    chat_list_col = ft.Column(scroll=ft.ScrollMode.AUTO, spacing=4, expand=True)

    # -----------------------------------------------------------------------
    # Sidebar — configuration
    # -----------------------------------------------------------------------
    api_key_status = ft.Row(
        [
            ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREEN_400, size=16),
            ft.Text("API Key Loaded", color=ft.Colors.GREEN_400, size=12),
        ]
        if get_api_key()
        else [
            ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=ft.Colors.ORANGE_400, size=16),
            ft.Text("API Key Required — set NVIDIA_API_KEY", color=ft.Colors.ORANGE_400, size=12),
        ],
        spacing=4,
    )

    env_model = get_env_model()
    _model_opts = list(MODEL_OPTIONS)
    if env_model not in _model_opts:
        _model_opts.insert(0, env_model)

    model_dropdown = ft.Dropdown(
        label="Model",
        value=env_model,
        options=[ft.dropdown.Option(m) for m in _model_opts],
        dense=True,
        text_size=12,
        label_style=ft.TextStyle(size=12, color=ft.Colors.GREY_400),
        bgcolor=ft.Colors.GREY_800,
        border_radius=8,
    )

    config_tile = ft.ExpansionTile(
        title=ft.Text("⚙️  Configuration", size=13),
        controls=[
            ft.Container(
                ft.Column([api_key_status, model_dropdown], spacing=8),
                padding=ft.padding.only(left=8, right=8, bottom=8),
            )
        ],
        initially_expanded=False,
        tile_padding=ft.padding.symmetric(horizontal=8),
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

    connect_mcp_btn = ft.ElevatedButton(
        "🔌  Connect MCP Servers",
        icon=ft.Icons.ELECTRICAL_SERVICES,
        on_click=lambda e: asyncio.ensure_future(_connect_mcp()),
        style=ft.ButtonStyle(bgcolor=ft.Colors.BLUE_900),
    )

    mcp_tile = ft.ExpansionTile(
        title=ft.Text("🔌  MCP Servers", size=13),
        controls=[
            ft.Container(
                ft.Column([connect_mcp_btn, mcp_status_col], spacing=8),
                padding=ft.padding.only(left=8, right=8, bottom=8),
            )
        ],
        initially_expanded=False,
        tile_padding=ft.padding.symmetric(horizontal=8),
    )

    # -----------------------------------------------------------------------
    # Sidebar layout
    # -----------------------------------------------------------------------
    new_chat_btn = ft.ElevatedButton(
        "➕  New Chat",
        icon=ft.Icons.ADD,
        expand=True,
        on_click=lambda e: asyncio.ensure_future(_new_chat()),
    )

    sidebar = ft.Container(
        ft.Column(
            [
                ft.Container(
                    ft.Text("💬  Chats", size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                    padding=ft.padding.only(top=16, left=12, bottom=8),
                ),
                ft.Container(
                    ft.Row([new_chat_btn]),
                    padding=ft.padding.symmetric(horizontal=8),
                ),
                ft.Divider(height=12, color=ft.Colors.GREY_700),
                ft.Container(chat_list_col, expand=True, padding=ft.padding.symmetric(horizontal=4)),
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
                    margin=ft.margin.only(left=8),
                ),
            ],
            spacing=8,
        ),
        padding=ft.padding.symmetric(horizontal=20, vertical=10),
        bgcolor=ft.Colors.GREY_850,
        border=ft.border.only(bottom=ft.BorderSide(1, ft.Colors.GREY_700)),
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
        padding=ft.padding.symmetric(horizontal=16, vertical=10),
        bgcolor=_INPUT_BAR_BG,
        border=ft.border.only(top=ft.BorderSide(1, ft.Colors.GREY_700)),
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
            btn = ft.TextButton(
                text=("▸  " if is_active else "    ") + chat["title"],
                data=chat["id"],
                style=ft.ButtonStyle(
                    color=ft.Colors.WHITE if is_active else ft.Colors.GREY_400,
                    bgcolor={
                        ft.ControlState.DEFAULT: ft.Colors.BLUE_900 if is_active else ft.Colors.TRANSPARENT,
                        ft.ControlState.HOVERED: ft.Colors.GREY_800,
                    },
                    padding=ft.padding.symmetric(horizontal=8, vertical=4),
                ),
                expand=True,
                on_click=lambda e: asyncio.ensure_future(_load_chat(e.control.data)),
            )
            chat_list_col.controls.append(btn)

    # -----------------------------------------------------------------------
    # Helper: rebuild chat_view from state["messages"]
    # -----------------------------------------------------------------------
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

        async def run_agent() -> None:
            try:
                agent = NIMAgent(config)
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
                page.open(ft.SnackBar(
                    ft.Text(f"Agent error: {exc}"),
                    bgcolor=ft.Colors.RED_900,
                ))

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
    async def _connect_mcp() -> None:
        connect_mcp_btn.disabled = True
        connect_mcp_btn.text = "Connecting..."
        page.update()
        try:
            await mcp_manager.connect_all()
            _refresh_mcp_status()
        except Exception as exc:
            page.open(ft.SnackBar(
                ft.Text(f"MCP connection error: {exc}"),
                bgcolor=ft.Colors.RED_900,
            ))
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    ft.app(target=main)
