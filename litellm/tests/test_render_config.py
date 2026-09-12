import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "render_config.py"
SPEC = importlib.util.spec_from_file_location("render_config", MODULE_PATH)
render_config = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(render_config)


BASE = """model_list:
  - model_name: quick
    litellm_params:
      model: ollama/test
  - model_name: coder
    litellm_params:
      model: ollama/coder
  - model_name: reasoning
    litellm_params:
      model: ollama/reasoning
  - model_name: vision
    litellm_params:
      model: ollama/vision

###########################################################
# General Settings
###########################################################

router_settings:
  routing_strategy: simple-shuffle
"""


def test_local_only_config_is_unchanged():
    assert render_config.render_config(BASE, {}) == BASE


def test_hybrid_promotes_hf_and_adds_ollama_fallbacks():
    rendered = render_config.render_config(
        BASE,
        {
            "INFERENCE_MODE": "hybrid",
            "HF_MODEL": "org/coder",
            "HF_INFERENCE_BASE_URL": "https://example.endpoints.huggingface.cloud/v1/",
            "HF_API_KEY": "secret",
        },
    )

    assert "model_name: coder-local" in rendered
    assert "model_name: reasoning-local" in rendered
    assert "model_name: vision-local" in rendered
    assert "model_name: coder\n" in rendered
    assert "openai/org/coder" in rendered
    assert "https://example.endpoints.huggingface.cloud/v1" in rendered
    assert "coder: [coder-local]" in rendered
    assert "reasoning: [reasoning-local]" in rendered
    assert "vision: [vision-local]" in rendered
    assert "secret" not in rendered


def test_role_specific_hf_models_override_generic_model():
    rendered = render_config.render_config(
        BASE,
        {
            "INFERENCE_MODE": "hybrid",
            "HF_MODEL": "org/general",
            "HF_CODER_MODEL": "org/coder",
            "HF_REASONING_MODEL": "org/reasoner",
            "HF_VISION_MODEL": "org/vision",
            "HF_INFERENCE_BASE_URL": "https://example.invalid/v1",
            "HF_API_KEY": "secret",
        },
    )
    assert "openai/org/coder" in rendered
    assert "openai/org/reasoner" in rendered
    assert "openai/org/vision" in rendered
    assert "openai/org/general" in rendered


def test_local_mode_never_adds_remote_models_even_if_credentials_exist():
    rendered = render_config.render_config(
        BASE,
        {
            "INFERENCE_MODE": "local",
            "HF_MODEL": "org/coder",
            "HF_INFERENCE_BASE_URL": "https://example.invalid/v1",
            "HF_API_KEY": "secret",
        },
    )
    assert rendered == BASE
    assert "remote-hf" not in rendered


@pytest.mark.parametrize(
    "env",
    [
        {"INFERENCE_MODE": "hybrid", "HF_MODEL": "org/coder", "HF_API_KEY": "secret"},
        {"INFERENCE_MODE": "hybrid", "HF_MODEL": "org/coder", "HF_INFERENCE_BASE_URL": "https://example.invalid/v1"},
        {"INFERENCE_MODE": "hybrid", "HF_INFERENCE_BASE_URL": "https://example.invalid/v1", "HF_API_KEY": "secret"},
    ],
)
def test_partial_hf_configuration_is_rejected(env):
    with pytest.raises(ValueError, match="configured together"):
        render_config.render_config(BASE, env)
