"""Every symbol and signature this skill teaches, asserted against the installed packages.

Unpinned deps (tests/requirements.txt): a red run here means evaluatorq or
orq-ai-sdk moved and the skill's markdown is now wrong — re-probe and update it.
Nothing in this file makes a network call; it only inspects what is importable.
"""

import inspect

import pytest


def params(fn):
    return inspect.signature(fn).parameters


# --- resources/inputs-and-data.md ------------------------------------------


def test_top_level_exports_exist():
    import evaluatorq

    for name in (
        "evaluatorq",
        "job",
        "DataPoint",
        "DatasetIdInput",
        "ExperimentInput",
        "EvaluationResult",
        "EvaluationResultCell",
        "ScorerParameter",
        "llm_jury",
        "llm_jury_pairwise",
        "build_report",
        "PRESETS",
        "get_preset",
    ):
        assert hasattr(evaluatorq, name), f"evaluatorq.{name} is gone — the skill still teaches it"


@pytest.mark.parametrize("name,default", [("inference", True), ("print_results", True)])
def test_evaluatorq_kwarg_defaults(name, default):
    from evaluatorq import evaluatorq

    p = params(evaluatorq)
    assert name in p, f"`{name}` is documented but not a parameter"
    assert p[name].default == default


def test_datapoint_parallelism_still_resolves_to_ten():
    """The signature says `None`; the skill documents the effective default."""
    from evaluatorq.common.parallelism import resolve_datapoint_parallelism

    assert resolve_datapoint_parallelism(None, None, default=10, caller="test") == 10
    assert resolve_datapoint_parallelism(None, 3, default=10, caller="test") == 3, (
        "`parallelism` must still be honoured as the deprecated alias"
    )


def test_evaluatorq_accepts_the_documented_knobs():
    from evaluatorq import evaluatorq

    p = params(evaluatorq)
    for name in ("data", "jobs", "evaluators", "llm_parallelism", "path", "description"):
        assert name in p, f"`{name}` is documented but not a parameter"


def test_parallelism_is_still_a_deprecated_alias():
    from evaluatorq import evaluatorq

    assert "parallelism" in params(evaluatorq), (
        "the skill says `parallelism` still works as a deprecated alias"
    )


def test_datapoint_and_platform_input_fields():
    from evaluatorq import DataPoint, DatasetIdInput, ExperimentInput

    assert {"inputs", "expected_output"} <= set(DataPoint.model_fields)
    assert {"dataset_id", "include_messages"} <= set(DatasetIdInput.model_fields)
    assert {"experiment_id", "run_id"} <= set(ExperimentInput.model_fields)


def test_result_tree_field_names():
    from evaluatorq import DataPointResult, EvaluationResult, EvaluatorScore, JobResult

    assert {"data_point", "error", "job_results"} <= set(DataPointResult.model_fields)
    assert {"job_name", "output", "error", "evaluator_scores"} <= set(JobResult.model_fields)
    assert {"evaluator_name", "error", "score"} <= set(EvaluatorScore.model_fields)
    assert {"value", "explanation", "pass_", "token_usage", "raw_output"} <= set(
        EvaluationResult.model_fields
    )


def test_check_pass_failures_gates_on_errors_only_when_asked():
    from evaluatorq.evaluatorq import check_pass_failures

    assert params(check_pass_failures)["treat_errors_as_failure"].default is False


# --- resources/judges-and-juries.md ----------------------------------------


@pytest.mark.parametrize(
    "name,default",
    [
        ("max_tokens", 8000),
        ("timeout_ms", 90000),
        ("assignment", "all"),
        ("structured_output", True),
    ],
)
def test_llm_jury_documented_defaults(name, default):
    from evaluatorq import llm_jury

    p = params(llm_jury)
    assert name in p, f"`{name}` is documented but not an `llm_jury` parameter"
    assert p[name].default == default


def test_llm_jury_panel_arguments():
    from evaluatorq import llm_jury

    p = params(llm_jury)
    for name in (
        "criteria",
        "judges",
        "preset",
        "repetitions",
        "replacement_judges",
        "min_successful_judges",
        "aggregator",
        "reasoning_effort",
        "extra_kwargs",
        "extra_body",
        "client",
        "prompt",
    ):
        assert name in p, f"`{name}` is documented but not an `llm_jury` parameter"


def test_jury_raw_output_keys_and_model():
    from evaluatorq.contracts import EVAL_ERROR_RAW_OUTPUT_KEY, JURY_RAW_OUTPUT_KEY, JuryResult

    assert JURY_RAW_OUTPUT_KEY == "jury"
    assert EVAL_ERROR_RAW_OUTPUT_KEY == "evaluation_error"
    assert {"judges_configured", "judges_succeeded", "raw_agreement", "stats", "votes"} <= set(
        JuryResult.model_fields
    )


def test_documented_presets_are_all_seated():
    from evaluatorq import PRESETS

    names = set(PRESETS)
    for preset in (
        "Balanced Trio",
        "Strong Jury",
        "Open-Weight / Portable",
        "EU Region",
        "Single-Provider Trio",
    ):
        assert preset in names, f"preset {preset!r} is documented but no longer shipped"


# --- resources/tuning.md ---------------------------------------------------


def test_reasoning_effort_is_never_hardcoded_in_the_skill():
    """The docs must name no effort value — the ladder is per model and per release."""
    from pathlib import Path

    skill = Path(__file__).resolve().parents[1]
    offenders = [
        f"{p.relative_to(skill)}:{i}"
        for p in skill.rglob("*.md")
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if "reasoning_effort" in line and any(f'"{v}"' in line for v in ("minimal", "low", "medium", "high"))
    ]
    assert not offenders, f"hardcoded reasoning effort: {offenders}"


def test_catalogue_helpers_exist():
    from evaluatorq.common.model_catalogue import (
        ModelInfo,
        get_model_info,
        register_model,
        validate_reasoning_effort,
    )

    assert "reasoning_efforts" in ModelInfo._fields   # NamedTuple
    assert inspect.iscoroutinefunction(get_model_info)
    assert inspect.iscoroutinefunction(validate_reasoning_effort)
    assert callable(register_model)


def test_llm_slot_is_where_the_skill_says():
    from evaluatorq.common.llm_limit import llm_slot

    assert callable(llm_slot)


def test_llm_call_config_pipeline_knobs():
    from evaluatorq.contracts import LLMCallConfig

    f = LLMCallConfig.model_fields
    assert f["max_tokens"].default == 10000
    assert f["timeout_ms"].default == 90000
    assert f["retry_count"].default == 1
    assert f["temperature"].default is None, "the skill says unset means not sent"
    assert f["reasoning_effort"].default is None


def test_evaluator_config_inherits_reasoning_effort():
    from evaluatorq.contracts import LLMCallConfig
    from evaluatorq.redteam import EvaluatorConfig

    assert issubclass(EvaluatorConfig, LLMCallConfig)
    assert "reasoning_effort" in EvaluatorConfig.model_fields


# --- the orq platform evaluator scorer -------------------------------------


def test_evals_invoke_result_is_flat():
    """`result.value`, never `result.value.value` — the fix this skill carries."""
    from orq_ai_sdk.models import EvaluationResult as SDKEvaluationResult

    fields = set(SDKEvaluationResult.model_fields)
    assert {"value", "explanation", "passed", "status", "type"} <= fields


def test_evals_invoke_is_the_method_name():
    from orq_ai_sdk import Orq

    # `evals` is lazily attached, so it is an annotation rather than a class attribute.
    assert "evals" in Orq.__annotations__, (
        "the skill forbids `orq.evaluators` and teaches `orq.evals`"
    )
