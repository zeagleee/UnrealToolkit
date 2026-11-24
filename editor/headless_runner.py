"""Entry script for `ue.py py --headless` (UnrealEditor-Cmd -run=pythonscript).

Reads the request JSON named by AGENT_HEADLESS_REQUEST, executes it with the same executor as the
live bridge, and writes the response next to it.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent_bridge  # noqa: E402

request_path = os.environ["AGENT_HEADLESS_REQUEST"]
with open(request_path, "r", encoding="utf-8") as f:
    request = json.load(f)

response = agent_bridge.execute(request["code"], agent_bridge.new_namespace(), "<agent-headless>")
if response.pop("_generator", None) is not None:  # nothing ticks in a commandlet, so waits could never end
    response.update(ok=False, error="Scenarios (cells with a top-level yield) need the live editor; drop --headless.")
with open(request["response_path"], "w", encoding="utf-8") as f:
    json.dump(response, f, ensure_ascii=False, indent=1)
