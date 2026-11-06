"""Tool descriptions shown to agents, and parsing of the JSON actions they reply with.

Agents act by replying with ONE JSON object: {"tool": "<name>", "args": {...}}.
This works with any chat model, including small free ones without native tool calling.
"""
import json

from sandbox.world import TOOL_SPECS


def tool_manual():
    lines = []
    for name, (_service, desc, args) in TOOL_SPECS.items():
        sig = ", ".join(f'"{a}": "..."' for a in args)
        lines.append(f'- {name}: {desc}  args: {{{sig}}}')
    return "\n".join(lines)


def parse_action(text):
    """Return (action_dict, error). Takes the first JSON object with a "tool" key."""
    if not text:
        return None, "empty reply"
    decoder = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            obj, _ = decoder.raw_decode(text[i:])
            if isinstance(obj, dict) and isinstance(obj.get("tool"), str):
                args = obj.get("args", {})
                if not isinstance(args, dict):
                    args = {}
                return {"tool": obj["tool"].strip(), "args": args}, None
        except json.JSONDecodeError:
            pass
        i = text.find("{", i + 1)
    return None, "no JSON object with a \"tool\" key found"
