"""poc_shared_tools — the one shared library every POC Builder agent imports (Spec §7.1–7.3, §7.9).

Sub-modules: s3, metadata, secrets, guardrails, config. All tools are plain
functions that return dicts, raise ToolError, and never log secrets.
"""
from .config import Config, get_config
from .errors import ToolError

__all__ = ["Config", "get_config", "ToolError"]
