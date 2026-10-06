from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_production_compose_has_single_inference_boundary():
    compose = (ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
    assert "\n  ollama:" not in compose
    assert "\n  litellm:" not in compose
    assert "INFERENCE_BASE_URL" in compose
    assert "OPENAI_API_BASE_URL: ${INFERENCE_BASE_URL}" in compose


def test_local_model_provisioning_is_not_part_of_ai_stack():
    assert not (ROOT / "download_models.sh").exists()
    assert not (ROOT / "docker-compose.litellm-default.yaml").exists()
    assert not (ROOT / "litellm").exists()
