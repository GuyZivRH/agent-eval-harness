"""A single-case run should not serialize independent LLM judges."""

import threading

import score
from agent_eval.config import EvalConfig


def test_single_case_judges_run_concurrently_and_keep_names(tmp_path, monkeypatch):
    case_dir = tmp_path / "case-1"
    case_dir.mkdir()
    monkeypatch.setattr(score, "load_case_record", lambda *args, **kwargs: {})
    rendezvous = threading.Barrier(3, timeout=3)

    def judge(value):
        def run(*, outputs):
            rendezvous.wait()
            return value, f"judge {value}"
        return run

    judges = [
        (f"quality_{value}", judge(value), None, "llm", 1)
        for value in (1, 2, 3)
    ]
    config = EvalConfig(name="parallel-judges", skill="test")

    result = score.score_cases(judges, [case_dir], config)

    assert list(result["per_case"]["case-1"]) == [
        "quality_1", "quality_2", "quality_3"
    ]
    assert [result["per_case"]["case-1"][f"quality_{n}"]["value"]
            for n in (1, 2, 3)] == [1, 2, 3]
