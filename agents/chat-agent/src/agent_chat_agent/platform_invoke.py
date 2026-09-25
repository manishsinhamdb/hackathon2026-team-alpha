"""Compatibility shim: platform_invoke now lives in the shared package (poc_shared_tools.platform_invoke)
so every agent can use it. This module is an alias for the shared one, so:
  - `from agent_chat_agent import platform_invoke` / `import agent_chat_agent.platform_invoke` keep working;
  - tests that patch attributes here (e.g. `platform_invoke._http`) patch the real implementation, because
    this name resolves to the very same module object.
"""
from __future__ import annotations

import sys

from poc_shared_tools import platform_invoke as _shared

# Make `agent_chat_agent.platform_invoke` resolve to the shared module object itself, so attribute patches
# and internal references stay in sync (a plain re-export would create separate name bindings).
sys.modules[__name__] = _shared
