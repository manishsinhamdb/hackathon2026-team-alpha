from poc_shared_tools.errors import ToolError


class InfraError(ToolError):
    """Same shape as ToolError so callers handle both alike."""
