from __future__ import annotations

import asyncio
import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import aiofiles
from ..config import get_settings


@dataclass
class ToolDefinition:
    """Tool definition for the agent."""
    name: str
    description: str
    parameters: Dict[str, Any]

    def to_openai_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
            }
        }


class Workspace:
    """
    Sandboxed workspace for file operations.
    All paths are resolved relative to workspace root and validated.
    """

    def __init__(self, root: Optional[str] = None):
        settings = get_settings()
        if root:
            self.root = Path(root).resolve()
        else:
            self.root = settings.workspace_root

        # Ensure workspace exists
        self.root.mkdir(parents=True, exist_ok=True)

        # Allowed commands (can be overridden via settings)
        self.allowed_commands: Set[str] = settings.allowed_commands

    def _safe_path(self, path: str) -> Path:
        """Resolve path and ensure it's within workspace root.

        Security: Validates path BEFORE resolving to prevent symlink traversal attacks.
        Does not follow symlinks during validation.
        """
        target = Path(path)

        # Reject absolute paths
        if target.is_absolute():
            raise ValueError(f"Absolute paths not allowed: '{path}'")

        # Reject paths with parent directory traversal before resolution
        # This catches "../" attempts before any symlink resolution
        parts = target.parts
        if any(part == ".." for part in parts):
            raise ValueError(f"Path traversal not allowed: '{path}'")

        # Build full path relative to workspace root
        full_path = (self.root / target).resolve()

        # Final validation: ensure resolved path is within workspace root
        # This protects against symlinks that point outside the workspace
        if not full_path.is_relative_to(self.root):
            raise ValueError(f"Path '{path}' escapes workspace root (symlink check)")

        return full_path

    def _validate_command(self, command: str) -> List[str]:
        """Parse and validate command against allowlist."""
        parts = shlex.split(command)
        if not parts:
            raise ValueError("Empty command")
        if parts[0] not in self.allowed_commands:
            raise ValueError(
                f"Command '{parts[0]}' not allowed. "
                f"Allowed: {sorted(self.allowed_commands)}"
            )
        return parts

    async def list_dir(self, directory: str = ".") -> str:
        """List files and directories asynchronously."""
        safe_path = self._safe_path(directory)
        if not safe_path.exists():
            return f"Error: Directory '{directory}' does not exist"
        if not safe_path.is_dir():
            return f"Error: '{directory}' is not a directory"
        try:
            items = sorted(os.listdir(safe_path))
            return json.dumps(items)
        except Exception as e:
            return f"Error: {e}"

    async def read_file(self, file_path: str) -> str:
        """Read file content asynchronously."""
        safe_path = self._safe_path(file_path)
        if not safe_path.exists():
            return f"Error: File '{file_path}' does not exist"
        if not safe_path.is_file():
            return f"Error: '{file_path}' is not a file"
        try:
            async with aiofiles.open(safe_path, "r", encoding="utf-8") as f:
                return await f.read()
        except Exception as e:
            return f"Error: {e}"

    async def write_file(self, file_path: str, content: str) -> str:
        """Write content to file asynchronously."""
        safe_path = self._safe_path(file_path)
        try:
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(safe_path, "w", encoding="utf-8") as f:
                await f.write(content)
            return f"Successfully wrote to {file_path}"
        except Exception as e:
            return f"Error: {e}"

    def run_command(self, command: str, cwd: str = ".") -> str:
        """Run a validated shell command."""
        try:
            parts = self._validate_command(command)
            safe_cwd = self._safe_path(cwd)

            result = subprocess.run(
                parts,
                capture_output=True,
                text=True,
                cwd=safe_cwd,
                timeout=30
            )
            return (
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}\n"
                f"Return code: {result.returncode}"
            )
        except ValueError as e:
            return f"Validation Error: {e}"
        except subprocess.TimeoutExpired:
            return "Error: Command timed out after 30 seconds"
        except Exception as e:
            return f"Error: {e}"

    def get_tools(self) -> List[ToolDefinition]:
        """Get all workspace tools as definitions."""
        return [
            ToolDefinition(
                name="list_dir",
                description="List files and directories in the given path.",
                parameters={
                    "type": "object",
                    "properties": {
                        "directory": {
                            "type": "string",
                            "description": "The directory path to list (relative to workspace root)."
                        }
                    },
                    "required": ["directory"]
                }
            ),
            ToolDefinition(
                name="read_file",
                description="Read and return the content of a file.",
                parameters={
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "The file path to read (relative to workspace root)."
                        }
                    },
                    "required": ["file_path"]
                }
            ),
            ToolDefinition(
                name="write_file",
                description="Write content to a file, overwriting if it exists.",
                parameters={
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "The file path to write (relative to workspace root)."
                        },
                        "content": {
                            "type": "string",
                            "description": "The content to write to the file."
                        }
                    },
                    "required": ["file_path", "content"]
                }
            ),
            ToolDefinition(
                name="run_command",
                description="Run a validated shell command in the workspace.",
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The shell command to execute."
                        },
                        "cwd": {
                            "type": "string",
                            "description": "Working directory (relative to workspace root).",
                            "default": "."
                        }
                    },
                    "required": ["command"]
                }
            )
        ]

    async def execute_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        """Execute a workspace tool by name asynchronously."""
        if name == "list_dir":
            return await self.list_dir(**arguments)
        elif name == "read_file":
            return await self.read_file(**arguments)
        elif name == "write_file":
            return await self.write_file(**arguments)
        elif name == "run_command":
            return self.run_command(**arguments)
        else:
            return f"Error: Unknown tool '{name}'"