# Catalog: build-evaluator

Tests for [`skills/build-evaluator/SKILL.md`](../../../skills/build-evaluator/SKILL.md).
Source scenarios: [`../../skills.md`](../../skills.md) (build-evaluator) and
[`../../mcp-tools.md`](../../mcp-tools.md) (evaluator tools).

## Functional cases

### F1. Create + read back a Python (code) evaluator
- **Operation:** `create_python_eval` with key `orq-skills-test-py-eval`, code that returns
  `True` when `log['output']` is valid JSON, then read it back.
- **Inputs:** code `import json\n\ndef evaluate(log):\n    try:\n        json.loads(log["output"]); return True\n    except Exception:\n        return False`
- **Verify:** read-back returns the stored code. The skill documents the read tool as `evaluator_get`
  — confirm the **actual** read tool that works (`get_python_eval`). Record the working tool name.
- **Cleanup:** evaluator — `delete_entity(type=evaluator, id=…)`.
- **Drift watch:** `evaluator_get` is referenced in `skills/build-evaluator/SKILL.md:97` and several
  `resources/api-reference.md` files but is not exposed by the server (`get_python_eval` /
  `get_llm_eval` are). Report as DRIFT if `evaluator_get` is unavailable.

### F2. Create + read back an LLM judge evaluator
- **Operation:** `create_llm_eval` with key `orq-skills-test-llm-eval`, a minimal binary Pass/Fail
  judge prompt using `{{log.output}}`, then read it back (`get_llm_eval`).
- **Verify:** read-back returns the prompt and model. Confirm template variables the skill documents
  (`{{log.input}}`, `{{log.output}}`, `{{log.reference}}`) are accepted.
- **Cleanup:** evaluator — `delete_entity(type=evaluator, id=…)`.

### F3. Judge model is selectable
- **Operation:** `list_models(modelType=chat)`.
- **Verify:** the response is non-empty and includes a capable judge model (gpt-4.1 class). Confirms
  Phase 6 "select judge model" is actionable.
- **Cleanup:** none (read-only).

## Behavioural scenarios

### B1. JSON validity → code eval, not LLM judge
- **Type:** explicit
- **Trigger:** "Build an evaluator that checks if output is valid JSON"
- **Expected routing:** build-evaluator
- **PASS:** recommends a **code-based** evaluator (`create_python_eval`); explains code checks are
  cheaper/deterministic than an LLM judge for this.
- **Anti-patterns (FAIL):** proposes an LLM-as-Judge for a deterministic check; uses a Likert scale.

### B2. Tone + helpfulness → split, binary
- **Type:** explicit
- **Trigger:** "Build an evaluator for tone and helpfulness"
- **Expected routing:** build-evaluator
- **PASS:** suggests **splitting into separate evaluators** (one criterion each); defaults to binary
  Pass/Fail; mentions validating against human labels (TPR/TNR).
- **Anti-patterns (FAIL):** bundles both criteria into one judge; uses a 1-5/1-10 scale; uses generic
  metrics (BERTScore/ROUGE/"coherence").

### B3. Implicit — a need, not the artifact
- **Type:** implicit
- **Trigger:** "My LLM sometimes returns malformed JSON and I want to automatically catch it in my pipeline."
- **Expected routing:** build-evaluator
- **PASS:** routes here on the description alone; recommends a code-based check for JSON validity.
- **Anti-patterns (FAIL):** misroutes to optimize-prompt / setup-observability; jumps to an LLM judge.

## Negative controls (must NOT fire build-evaluator)

### N1. Plain coding request
- **Type:** negative
- **Trigger:** "Write me a Python function that parses a JSON string and returns a dict."
- **Expected routing:** none (ordinary coding) — build-evaluator must not fire.
- **PASS:** answers as normal coding; does not invoke build-evaluator or talk about TPR/TNR judges.
- **Fired = FAIL:** treating a utility-function request as an evaluator build.
