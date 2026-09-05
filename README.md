# NVIDIA NIM Coding Agent

A powerful, desktop-based AI coding assistant powered by NVIDIA NIM (NVIDIA Inference Microservices) and built with a modern Flet UI. This agent acts as a pair-programmer that can write code, analyze your local workspace, run shell commands, and dynamically connect to Model Context Protocol (MCP) servers to expand its toolset.

## ✨ Features & Agent Capabilities

- **NVIDIA NIM Integration:** Leverages high-performance, low-latency LLMs served via NVIDIA's API.
- **Local Workspace Management:** Reads, analyzes, and modifies code files directly within your designated workspace.
- **Shell Execution:** Executes local CLI commands (compiling, testing, git operations) autonomously from the chat interface.
- **MCP Server Support:** Seamlessly connects to standard MCP servers (e.g., `sequential-thinking`, `context7`) on boot, instantly expanding the agent's toolset.
- **Persistent Chat History:** Saves conversations locally so you can resume your projects anytime.
- **In-App Configuration:** Manage your custom models, API keys, and external MCP server JSON configs directly from the UI sidebar.

## 📂 Project Structure

```text
├── main.py                           # Flet desktop UI and application entry point
├── mcp_config.json                   # Configuration definitions for external MCP servers
├── chats/                            # Persisted conversation history (JSON)
└── src/
    └── chatbot/
        ├── application/
        │   └── agent.py              # Core NIMAgent orchestrator (manages LLM & tools)
        ├── domain/
        │   └── models.py             # Domain schemas (Messages, Tool definitions)
        └── infrastructure/
            ├── llm/nim.py            # NVIDIA NIM API provider and streaming logic
            ├── mcp/client.py         # MCP Client connection manager and subprocess handler
            └── workspace/local.py    # Local file system operations and shell execution
```

## 🚀 Setup & Installation

### Option 1: Download the Standalone Executable
If you just want to use the agent without installing Python dependencies, you can download the pre-compiled `.exe`.

1. Download the latest `.exe` package from the Releases page.
2. Get your NVIDIA API Key:
   - Visit [build.nvidia.com](https://build.nvidia.com/).
   - Create an account or log in.
   - Generate an API Key from your dashboard.
3. Run the `.exe`. 
4. In the left sidebar under **⚙️ Configuration**, paste your NVIDIA API Key into the designated field. (This will be securely saved locally to an `.env` file).

### Option 2: Run from Source
1. Clone the repository and navigate to the directory.
2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```
   *(Ensure you have `flet`, `openai`, and `pydantic` installed)*
3. Run the application:
   ```bash
   python main.py
   ```

## 🛠️ Building the `.exe` from Source

If you want to package the application into a standalone Windows executable yourself, you can use Flet's built-in packager (which wraps PyInstaller).

1. Ensure Flet is installed in your environment:
   ```bash
   pip install flet
   ```
2. Run the `flet pack` command:
   ```bash
   flet pack main.py --name "NVIDIA_NIM_Agent"
   ```
   *(Note: You can also append `--icon assets/icon.ico` if you add an icon file to the repository).*
3. Once the build finishes, you will find your compiled executable inside the `dist/` folder. You can now distribute this single `.exe` file!
