from tiresias_agent.config import LoopBudgets


def test_loop_budgets_defaults_match_architecture():
    b = LoopBudgets.from_env({})
    assert (b.max_causal_depth, b.max_branches_per_level, b.run_seconds) == (5, 3, 600.0)


def test_loop_budgets_are_env_tunable_and_clamped():
    b = LoopBudgets.from_env(
        {"MAX_CAUSAL_DEPTH": "2", "MAX_BRANCHES_PER_LEVEL": "99", "RUN_BUDGET_SECONDS": "x"}
    )
    assert (b.max_causal_depth, b.max_branches_per_level, b.run_seconds) == (2, 6, 600.0)
