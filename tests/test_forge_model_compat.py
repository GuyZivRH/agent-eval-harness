from agent_eval.openshell.run import build_openclaw_eval_config


def test_preserves_image_model_compatibility_and_reasoning_parameters():
    model = {
        "id": "glm-test",
        "reasoning": True,
        "contextWindow": 262144,
        "maxTokens": 65536,
        "compat": {
            "maxTokensField": "max_completion_tokens",
            "supportsReasoningEffort": True,
        },
        "params": {"reasoning_effort": "high"},
    }
    config, _ = build_openclaw_eval_config(
        {"inference": {"baseUrl": "https://model.example/v1", "models": [model]}},
        "inference/glm-test",
    )
    actual = config["models"]["providers"]["inference"]["models"][0]
    for key in ("reasoning", "contextWindow", "maxTokens", "compat", "params"):
        assert actual.get(key) == model[key]
