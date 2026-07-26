import streamlit as st
import asyncio
import os
import json
import uuid
import glob
import queue
import threading
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

from src.chatbot.application.agent import NIMAgent, AgentConfig, AgentEvent, run_agent
from src.chatbot.domain.models import Message, Role
from src.chatbot.config import get_settings

# Load environment variables
load_dotenv()

st.set_page_config(
    page_title="NVIDIA NIM Coding Agent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

CHATS_DIR = "chats"
os.makedirs(CHATS_DIR, exist_ok=True)

# ============================================================
# Session Initialization
# ============================================================
for key in ["session_id", "messages", "timestamp", "processing", "cancel_requested", "mcp_manager"]:
    if key not in st.session_state:
        if key == "session_id":
            st.session_state.session_id = str(uuid.uuid4())
        elif key == "messages":
            st.session_state.messages = []
        elif key == "timestamp":
            st.session_state.timestamp = datetime.now().isoformat()
        elif key == "processing":
            st.session_state.processing = False
        elif key == "cancel_requested":
            st.session_state.cancel_requested = False
        elif key == "mcp_manager":
            from src.chatbot.infrastructure.mcp.client import MCPClientManager
            settings = get_settings()
            st.session_state.mcp_manager = MCPClientManager(str(settings.mcp_config_path))

# ============================================================
# Session Management
# ============================================================
def save_chat():
    if not st.session_state.get("messages"):
        return
    file_path = os.path.join(CHATS_DIR, f"{st.session_state.session_id}.json")
    with open(file_path, "w") as f:
        json.dump({
            "id": st.session_state.session_id,
            "timestamp": st.session_state.get("timestamp", datetime.now().isoformat()),
            "messages": st.session_state.messages
        }, f, indent=2)

def load_chat(session_id: str):
    file_path = os.path.join(CHATS_DIR, f"{session_id}.json")
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            data = json.load(f)
            st.session_state.session_id = data["id"]
            st.session_state.messages = data["messages"]
            st.session_state.timestamp = data["timestamp"]

def new_chat():
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.messages = []
    st.session_state.timestamp = datetime.now().isoformat()
    st.session_state.processing = False
    st.session_state.cancel_requested = False

def get_chat_history():
    chats = []
    for f in glob.glob(os.path.join(CHATS_DIR, "*.json")):
        try:
            with open(f, "r") as file:
                data = json.load(file)
                title = "New Chat"
                for msg in data.get("messages", []):
                    if msg["role"] == "user":
                        title = msg["content"][:40] + ("..." if len(msg["content"]) > 40 else "")
                        break
                chats.append({"id": data["id"], "timestamp": data.get("timestamp", ""), "title": title})
        except Exception:
            pass
    chats.sort(key=lambda x: x["timestamp"], reverse=True)
    return chats

def get_api_key():
    try:
        key = st.secrets.get("NVIDIA_API_KEY")
    except Exception:
        key = None
    if key is None:
        key = os.environ.get("NVIDIA_API_KEY", "")
    return key


# ============================================================
# Sidebar
# ============================================================
with st.sidebar:
    st.title("💬 Chats")
    st.button("➕ New Chat", use_container_width=True, on_click=new_chat, disabled=st.session_state.processing)
    st.divider()

    chats = get_chat_history()
    if chats:
        for chat in chats:
            is_active = chat["id"] == st.session_state.session_id
            btn_label = f"{'▸ ' if is_active else '  '}{chat['title']}"
            if st.button(btn_label, key=f"chat_{chat['id']}", use_container_width=True,
                         type="primary" if is_active else "secondary"):
                load_chat(chat['id'])
                st.rerun()
    else:
        st.caption("No chats yet. Start a new conversation!")
    st.divider()

    with st.expander("⚙️ Configuration", expanded=False):
        api_key = get_api_key()
        if not api_key:
            st.warning("⚠️ API Key Required")
            st.caption("Set `NVIDIA_API_KEY` in `.streamlit/secrets.toml` or environment")
        else:
            st.success("✅ API Key Loaded")
        st.markdown("")

        env_model = os.environ.get("MODEL", "meta/llama-3.1-70b-instruct")
        model_options = [
            "meta/llama-3.1-70b-instruct",
            "meta/llama-3.1-405b-instruct",
            "mistralai/mixtral-8x7b-instruct-v0.1"
        ]
        if env_model not in model_options:
            model_options.insert(0, env_model)
        model = st.selectbox("Model", model_options, index=model_options.index(env_model),
                             disabled=st.session_state.processing, key="_model_select")

    with st.expander("🔌 MCP Servers", expanded=False):
        mcp_manager = st.session_state.mcp_manager
        if st.button("🔌 Connect MCP Servers", use_container_width=True, type="primary",
                     disabled=st.session_state.processing):
            with st.spinner("Connecting..."):
                try:
                    asyncio.run(mcp_manager.connect_all())
                except Exception as e:
                    st.error(f"Connection error: {e}")
            st.success("Connected!")
            st.rerun()

        connected = mcp_manager.get_connected_servers()
        if connected:
            st.markdown("**Connected:**")
            for name, count in connected.items():
                st.markdown(f"✅ **{name}** ({count} tools)")
        else:
            st.info("No servers connected.")


# ============================================================
# Main Chat Area
# ============================================================
st.title("🤖 NVIDIA NIM Coding Agent")
st.caption("Powered by NIM, MCP & Streamlit")

if not st.session_state.messages:
    st.markdown("---")
    st.markdown("### Welcome to NIM Coding Agent")
    st.markdown("Ask me to write code, explore files, run commands, or help with any development task.")
    st.markdown("---")

for msg in st.session_state.messages:
    if msg["role"] == "user":
        with st.chat_message("user", avatar="👤"):
            st.markdown(msg["content"])
    elif msg["role"] == "assistant":
        if msg.get("content"):
            with st.chat_message("assistant", avatar="🤖"):
                st.markdown(msg["content"])
    elif msg["role"] == "tool_executing":
        with st.chat_message("assistant", avatar="🛠️"):
            st.markdown(f"**Executing:** `{msg['name']}`")
            st.json(msg['arguments'])


# ============================================================
# Chat Input — disabled when processing
# ============================================================
prompt = st.chat_input(
    "Ask me to edit code, run a task, or explore files...",
    key="chat_input",
    disabled=st.session_state.processing
)


# ============================================================
# Process new user message
# ============================================================
if prompt and not st.session_state.processing:
    api_key = get_api_key()
    if not api_key:
        st.warning("⚠️ NVIDIA_API_KEY not configured.")
        st.stop()

    st.session_state.messages.append({"role": "user", "content": prompt})
    save_chat()
    st.session_state.processing = True
    st.rerun()


# ============================================================
# Agent Execution with st.write_stream streaming
# ============================================================
if st.session_state.processing and st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
    api_key = get_api_key()
    model = st.session_state.get("_model_select", os.environ.get("MODEL", "meta/llama-3.1-70b-instruct"))

    # Convert messages for agent
    agent_messages = []
    for m in st.session_state.messages:
        if m["role"] == "user":
            agent_messages.append(Message.user(m["content"]))
        elif m["role"] == "assistant":
            if m.get("content"):
                msg = Message.assistant(content=m["content"])
                if m.get("tool_calls"):
                    msg.tool_calls = m["tool_calls"]
                agent_messages.append(msg)
        elif m["role"] == "tool":
            agent_messages.append(Message.tool_result(
                tool_call_id=m.get("tool_call_id", ""),
                name=m.get("name", ""),
                content=m.get("content", "")
            ))

    config = AgentConfig(api_key=api_key, model=model)

    # --- Show Stop button ---
    col1, col2, col3 = st.columns([3, 2, 3])
    with col2:
        if st.button("⏹ Stop Generation", use_container_width=True, type="primary", key="stop_btn"):
            st.session_state.cancel_requested = True
            st.rerun()

    # --- Streaming generator using thread + queue ---
    token_q = queue.Queue()
    final_result = [None]
    error_msg = [None]

    def run_agent_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def execute():
                agent = NIMAgent(config)
                await agent.initialize()
                try:
                    def emit_token(event):
                        token_q.put(event, timeout=5)
                    result = await agent.run_stream(agent_messages, emit_token)
                    token_q.put("__DONE__", timeout=5)
                    return result
                finally:
                    await agent.close()
            final_result[0] = loop.run_until_complete(execute())
        except Exception as e:
            error_msg[0] = str(e)
            token_q.put("__ERROR__")
        finally:
            loop.close()

    thread = threading.Thread(target=run_agent_thread, daemon=True)
    thread.start()

    # Generator for st.write_stream — yields string tokens
    def token_generator():
        accumulated = ""
        while thread.is_alive() or not token_q.empty():
            # Check for cancel
            if st.session_state.cancel_requested:
                thread.join(timeout=0.5)
                break

            try:
                item = token_q.get(timeout=0.2)
            except queue.Empty:
                continue

            if isinstance(item, AgentEvent):
                if item.role == "assistant" and item.content:
                    accumulated += item.content
                    if accumulated.strip():
                        yield accumulated + " ▌"
                elif item.role == "tool_executing":
                    yield f"\n\n🛠️ **Executing:** `{item.name}`"
                    if item.arguments:
                        yield "\n\n" + json.dumps(item.arguments, indent=2) + "\n\n"
            elif item == "__DONE__":
                break
            elif item == "__ERROR__":
                break

        # Yield final accumulated text without cursor
        if accumulated.strip():
            yield accumulated

    # Render using st.write_stream — this is Streamlit's native streaming API
    with st.chat_message("assistant", avatar="🤖"):
        streamed_text = st.write_stream(token_generator)

    # Wait for thread to fully complete
    thread.join(timeout=10)

    # Handle result
    if final_result[0] is not None:
        # Update session state
        new_messages = []
        for m in final_result[0]:
            if m.role == Role.SYSTEM:
                continue
            elif m.role == Role.USER:
                new_messages.append({"role": "user", "content": m.content})
            elif m.role == Role.ASSISTANT:
                msg_dict = {"role": "assistant", "content": m.content}
                if m.tool_calls:
                    msg_dict["tool_calls"] = m.tool_calls
                new_messages.append(msg_dict)
            elif m.role == Role.TOOL:
                new_messages.append({
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "name": m.name,
                    "content": m.content
                })
        st.session_state.messages = new_messages
    elif error_msg[0]:
        st.error(f"Agent error: {error_msg[0]}")

    save_chat()
    st.session_state.processing = False
    st.session_state.cancel_requested = False
    st.rerun()