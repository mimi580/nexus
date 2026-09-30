from pathlib import Path

from app.core.config import Settings
from app.core.logging import redact

REPO = Path(__file__).resolve().parent.parent


def test_settings_never_expose_secrets():
    settings = Settings(_env_file=None, anthropic_api_key="sk-test-123456789", email_api_key="secret")
    redacted = settings.redacted()
    assert redacted["anthropic_api_key"] == "set"
    assert "sk-test-123456789" not in str(redacted)


def test_log_redaction_covers_common_shapes():
    assert "sk-live-abcdef123456" not in redact("token sk-live-abcdef123456 used")
    assert "hunter2" not in redact('{"password": "hunter2"}')
    assert "abc.def" not in redact("Authorization: Bearer abc.def")


def test_env_example_exists_and_is_not_a_real_env():
    example = (REPO / ".env.example").read_text()
    assert "ANTHROPIC_API_KEY=" in example
    assert not (REPO / ".env").exists()
    assert ".env" in (REPO / ".gitignore").read_text()


def test_no_credentials_are_committed_in_source():
    suspicious = []
    for path in (REPO / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "sk-live" in stripped or "sk-ant-" in stripped:
                suspicious.append(f"{path}: {stripped[:60]}")
    assert suspicious == []
