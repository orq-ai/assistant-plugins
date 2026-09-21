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
    """The signature says `None`; `evaluatorq()` picks the default the skill documents.

    Asserting the resolver with our own `default=10` would pin nothing — the value
    that matters is the literal `evaluatorq()` hands it.
    """
    import re

    from evaluatorq import evaluatorq
    from evaluatorq.common.parallelism import resolve_datapoint_parallelism

    call = re.search(
        r"resolve_datapoint_parallelism\((.*?)\)", inspect.getsource(evaluatorq), re.S
    )
    assert call, "evaluatorq() no longer resolves its datapoint parallelism through the helper"
    assert "default=10" in call.group(1), (
        f"the skill documents a default of 10; evaluatorq() now passes {call.group(1).strip()}"
    )
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


def test_llm_slot_is_still_an_async_context_manager():
    """The skill's example is `async with llm_slot():` — `callable` would not catch a shape change."""
    from evaluatorq.common.llm_limit import llm_slot

    slot = llm_slot()
    assert hasattr(slot, "__aenter__") and hasattr(slot, "__aexit__"), (
        "llm_slot() no longer returns an async context manager"
    )


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

    # Constructing the client makes no request; `evals` is lazily attached, so only
    # an instance proves the attribute actually resolves.
    client = Orq(api_key="not-a-real-key")
    assert hasattr(client, "evals"), (
        "the skill forbids `orq.evaluators` and teaches `orq.evals`"
    )
    assert hasattr(client.evals, "invoke") and hasattr(client.evals, "invoke_async"), (
        "the skill teaches both evals.invoke() and evals.invoke_async()"
    )


# --- resources/inputs-and-data.md: the trace conversion path -----------------


def test_trace_helpers_are_importable_from_simulation():
    from evaluatorq.simulation import (
        datapoints_from_traces,
        extend_from_experiment,
        extend_from_traces,
        fetch_trace_conversations,
        summarize_conversations,
    )

    assert "num_datapoints" in params(extend_from_traces)
    assert {"limit", "search", "filters"} <= set(params(fetch_trace_conversations))
    assert "summaries" in params(datapoints_from_traces)
    for fn in (summarize_conversations, extend_from_experiment):
        assert inspect.iscoroutinefunction(fn)


def test_platform_input_defaults():
    from evaluatorq import DatasetIdInput, ExperimentInput

    assert DatasetIdInput.model_fields["include_messages"].default is False
    assert ExperimentInput.model_fields["run_id"].default is None


# --- resources/judges-and-juries.md: verdict modes and pairwise --------------


def test_llm_jury_verdict_mode_arguments():
    from evaluatorq import llm_jury

    p = params(llm_jury)
    for name in ("verdict_kind", "labels", "passing_labels", "threshold", "score_range"):
        assert name in p, f"`{name}` is documented as a verdict-mode argument but is gone"


def test_pairwise_surface():
    from evaluatorq import (
        PairwiseComparator,
        PairwiseReport,
        build_report,
        llm_jury_pairwise,
        run_pairwise,
    )

    assert "judges" in params(llm_jury_pairwise)
    assert callable(build_report) and callable(run_pairwise)
    assert hasattr(PairwiseComparator, "compare")
    assert {"a_win_rate", "inconclusive_rate"} <= set(PairwiseReport.model_fields)


def test_pairwise_run_store_entry_point():
    from evaluatorq.pairwise_run import new_run

    assert callable(new_run)


def test_bradley_terry_fit_is_where_the_skill_says():
    from evaluatorq.ranking import fit_bt

    assert callable(fit_bt)


# --- resources/tuning.md: catalogue registration ----------------------------


def test_register_model_takes_the_documented_model_info():
    from evaluatorq.common.model_catalogue import ModelInfo, register_model

    assert {
        "input_cost_per_1k",
        "output_cost_per_1k",
        "provider",
        "supports_responses",
        "reasoning_efforts",
    } <= set(ModelInfo._fields)
    assert len(params(register_model)) == 2, "register_model(model_id, ModelInfo)"


# --- the raw-dict job error contract ----------------------------------------


def test_a_job_reporting_its_own_error_fails_the_row_and_skips_evaluators():
    """`{"name", "output", "error"}` is the contract the skill teaches for a caught failure.

    Runs the real `evaluatorq()` — no API key, no network: one inline datapoint,
    one local job, one local scorer.
    """
    import asyncio

    from evaluatorq import DataPoint, evaluatorq

    scored = []

    async def failing_job(data, row):
        return {"name": "target", "output": None, "error": "boom"}

    async def clean_job(data, row):
        return {"name": "target", "output": "fine", "error": None}

    async def scorer(params):
        scored.append(params["output"])
        return {"value": 1.0, "explanation": ""}

    async def run(job):
        return await evaluatorq(
            "contract-check",
            data=[DataPoint(inputs={"q": "x"})],
            jobs=[job],
            evaluators=[{"name": "contract-scorer", "scorer": scorer}],
            print_results=False,
            _send_results=False,
        )

    failed = asyncio.run(run(failing_job))
    assert failed[0].job_results[0].error == "boom"
    assert not scored, "a job that reported an error must not cost an evaluator call"

    ok = asyncio.run(run(clean_job))
    assert ok[0].job_results[0].error is None
    assert scored == ["fine"]
