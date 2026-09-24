class ToolError(RuntimeError):
    """Typed tool failure. `retryable=True` means the caller may back off and retry (§5.3)."""
    def __init__(self, code: str, message: str, retryable: bool = False, detail: dict | None = None):
        self.code, self.retryable, self.detail = code, retryable, detail or {}
        super().__init__(f"{code}: {message}")
