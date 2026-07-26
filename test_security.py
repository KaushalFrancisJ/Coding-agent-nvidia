#!/usr/bin/env python
"""
Test security fixes for path traversal and command injection.
"""
import os
import tempfile
import subprocess
from pathlib import Path

# Test 1: Path traversal in src workspace
print("=" * 60)
print("TEST 1: Path traversal protection in src/workspace.py")
print("=" * 60)

from src.chatbot.domain.workspace import Workspace

ws = Workspace()
WORKSPACE_ROOT = ws.root

# Test absolute path rejection
try:
    ws._safe_path("/etc/passwd")
    print("FAIL: Absolute path was allowed")
except ValueError as e:
    print(f"PASS: Absolute path rejected - {e}")

# Test parent directory traversal
try:
    ws._safe_path("../../etc/passwd")
    print("FAIL: Parent traversal was allowed")
except ValueError as e:
    print(f"PASS: Parent traversal rejected - {e}")

# Test valid relative path
try:
    result = ws._safe_path("test.txt")
    print(f"PASS: Valid relative path accepted - {result}")
except ValueError as e:
    print(f"FAIL: Valid path rejected - {e}")

# Test symlink traversal (if we can create one)
print("\nTesting symlink protection...")
try:
    # Create a temp directory outside workspace
    with tempfile.TemporaryDirectory() as outside_dir:
        outside_file = Path(outside_dir) / "secret.txt"
        outside_file.write_text("SECRET DATA")

        # Try to create symlink inside workspace pointing outside
        symlink_path = WORKSPACE_ROOT / "evil_link.txt"
        try:
            symlink_path.symlink_to(outside_file)
            # Try to access via symlink
            result = ws._safe_path("evil_link.txt")
            # If we get here, check if it's actually outside
            if not result.is_relative_to(WORKSPACE_ROOT):
                print(f"FAIL: Symlink traversal worked - {result}")
            else:
                print(f"PASS: Symlink resolved within workspace")
        except (OSError, ValueError) as e:
            print(f"PASS: Symlink blocked - {e}")
        finally:
            if symlink_path.exists():
                symlink_path.unlink()
except Exception as e:
    print(f"SKIP: Could not test symlinks - {e}")

# Test 2: Command injection protection (MCP config)
print("\n" + "=" * 60)
print("TEST 2: MCP config validation")
print("=" * 60)

from src.chatbot.infrastructure.mcp.client import MCPClientManager

# Create a test MCP config with malicious entries
test_config = {
    "mcpServers": {
        "good": {
            "command": "npx",
            "args": ["-y", "@upstash/context7-mcp"]
        },
        "bad_command": {
            "command": "python",
            "args": ["-c", "import os; os.system('rm -rf /')"]
        },
        "bad_args": {
            "command": "npx",
            "args": ["../../evil", "-y", "malicious"]
        }
    }
}

import json
with open("test_mcp_config.json", "w") as f:
    json.dump(test_config, f)

manager = MCPClientManager("test_mcp_config.json")
configs = manager.load_config()

config_names = set(configs.keys())

if "good" in config_names:
    print("PASS: Good server loaded")
else:
    print("FAIL: Good server rejected")

if "bad_command" not in config_names:
    print("PASS: Bad command server rejected")
else:
    print("FAIL: Bad command server allowed")

# For bad_args, the suspicious arg should be filtered but server may still load
bad_args_config = configs.get("bad_args")
if bad_args_config and "../../evil" not in bad_args_config.args:
    print("PASS: Bad args filtered (server loads but suspicious arg removed)")
elif "bad_args" not in config_names:
    print("PASS: Bad args server rejected entirely")
else:
    print(f"FAIL: Bad args not properly filtered - {bad_args_config.args if bad_args_config else 'N/A'}")

# Cleanup
os.remove("test_mcp_config.json")

# Test 3: src workspace path validation (redundant but confirms)
print("\n" + "=" * 60)
print("TEST 3: src/workspace.py path validation (confirmation)")
print("=" * 60)

# Test absolute path rejection
try:
    ws._safe_path("/etc/passwd")
    print("FAIL: Absolute path allowed in src workspace")
except ValueError as e:
    print(f"PASS: Absolute path rejected - {e}")

# Test parent traversal rejection
try:
    ws._safe_path("../../etc/passwd")
    print("FAIL: Parent traversal allowed in src workspace")
except ValueError as e:
    print(f"PASS: Parent traversal rejected - {e}")

# Test valid path
try:
    result = ws._safe_path("README.md")
    print(f"PASS: Valid path accepted - {result}")
except ValueError as e:
    print(f"FAIL: Valid path rejected - {e}")

print("\n" + "=" * 60)
print("ALL TESTS COMPLETE")
print("=" * 60)