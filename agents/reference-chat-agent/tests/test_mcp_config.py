from pathlib import Path

import yaml

TEMPLATE_DIR = Path(__file__).resolve().parents[1]


def test_hello_world_has_no_github_mcp() -> None:
    config = yaml.safe_load((TEMPLATE_DIR / "agent.yaml").read_text())

    assert "mcp" not in config
    secrets = [
        secret
        for guest in config.get("sandboxes", {}).values()
        for secret in guest.get("secrets", [])
    ]
    assert "GITHUB_MCP_TOKEN" not in secrets
    assert "OPENAI_BASE_URL" not in secrets
    assert "ANTHROPIC_BASE_URL" not in secrets
