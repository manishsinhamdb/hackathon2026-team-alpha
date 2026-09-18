#!/usr/bin/env python3
"""Fails when the root agent.yaml and the agents/ directory disagree.

Every workspace under agents/ that holds an agent.yaml must be listed in the
root agent.yaml, and every listed path must exist. Run from the repository root.
Uses PyYAML when available and a minimal parser otherwise, so that contributors
need no extra dependency.
"""
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
text = (root / "agent.yaml").read_text()

listed = {}
try:
    import yaml

    data = yaml.safe_load(text) or {}
    for entry in data.get("agents") or []:
        listed[entry["name"]] = entry["path"]
except ModuleNotFoundError:
    name = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        m = re.match(r"-\s*name:\s*(\S+)", stripped)
        if m:
            name = m.group(1).strip("\"'")
            continue
        m = re.match(r"path:\s*(\S+)", stripped)
        if m and name:
            listed[name] = m.group(1).strip("\"'")
            name = None

on_disk = {
    p.parent.name: f"agents/{p.parent.name}"
    for p in (root / "agents").glob("*/agent.yaml")
}

errors = []
for name, path in on_disk.items():
    if name not in listed:
        errors.append(f"{path} exists but is not listed in the root agent.yaml")
for name, path in listed.items():
    if not (root / path / "agent.yaml").is_file():
        errors.append(f"root agent.yaml lists {name} at {path}, which has no agent.yaml")
    elif on_disk.get(name) != path:
        errors.append(f"root agent.yaml lists {name} at {path}, which does not match its folder")

if errors:
    print("Agent map check failed:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)

print(f"Agent map check passed: {len(listed)} agent(s) listed and present.")
