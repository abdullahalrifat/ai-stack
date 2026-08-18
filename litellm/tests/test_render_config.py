import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "render_config.py"
SPEC = importlib.util.spec_from_file_location("render_config", MODULE_PATH)
render_config = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(render_config)


BASE = """model_list:
  - model_name: local
    litellm_params:
      model: ollama/test

###########################################################
# General Settings
###########################################################
"""


def test_local_only_config_is_unchanged():
    assert render_config.render_config(BASE, {}) == BASE


def test_remote_providers_are_added_only_when_configured():
    rendered = render_config.render_config(
        BASE,
        {
            "ANTHROPIC_MODEL": "claude-example",
            "ANTHROPIC_API_KEY": "secret",
            "HF_MODEL": "org/coder",
            "HF_INFERENCE_BASE_URL": "https://example.endpoints.huggingface.cloud/v1/",
            "HF_API_KEY": "secret",
        },
    )

    assert "model_name: remote-claude" in rendered
    assert "anthropic/claude-example" in rendered
    assert "model_name: remote-hf" in rendered
    assert "openai/org/coder" in rendered
    assert "https://example.endpoints.huggingface.cloud/v1" in rendered
    assert "secret" not in rendered


@pytest.mark.parametrize(
    "env",
    [
        {"ANTHROPIC_MODEL": "claude-example"},
        {"ANTHROPIC_API_KEY": "secret"},
        {"HF_MODEL": "org/coder"},
        {
            "HF_MODEL": "org/coder",
            "HF_INFERENCE_BASE_URL": "https://example.invalid/v1",
        },
    ],
)
def test_partial_remote_provider_configuration_is_rejected(env):
    with pytest.raises(ValueError, match="must be configured together"):
        render_config.remote_model_entries(env)
