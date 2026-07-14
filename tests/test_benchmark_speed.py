from tools import benchmark_speed


def test_canonical_digest_ignores_only_runtime_fields():
    base = {"generated_at": "old", "flows": {"a": [1, 2]},
            "scenario": {"name": "baseline"}}
    changed_timestamp = {**base, "generated_at": "new"}
    changed_flow = {"generated_at": "new", "flows": {"a": [1, 3]},
                    "scenario": {"name": "baseline"}}
    assert benchmark_speed.canonical_digest(base) == \
        benchmark_speed.canonical_digest(changed_timestamp)
    assert benchmark_speed.canonical_digest(base) != \
        benchmark_speed.canonical_digest(changed_flow)
