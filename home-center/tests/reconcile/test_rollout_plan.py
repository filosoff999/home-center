from ops.reconcile.rollout import create_rollout_plan


def test_rollout_plan_starts_with_dc02():
    plan = create_rollout_plan("1d1ff0be759667c40361bbd04b9da273a778b9c8")

    assert plan.stages[0] == "dc02"
    assert plan.stages[1] == "canary-soak"
    assert plan.stages[2] == "dc01"


def test_rollout_requires_release_revision():
    try:
        create_rollout_plan("")
    except ValueError:
        return

    raise AssertionError("empty release revision must be rejected")
