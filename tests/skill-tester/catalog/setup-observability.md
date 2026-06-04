# Catalog: setup-observability

Tests for [`skills/setup-observability/SKILL.md`](../../../skills/setup-observability/SKILL.md).
Source scenarios: [`../../skills.md`](../../skills.md) (setup-observability).

## Functional cases

### F1. AI Router endpoint works
- **Operation:** call `https://api.orq.ai/v2/router` with a `provider/model` id (e.g. via
  `invoke_model` `openai/gpt-4.1-mini`, or an OpenAI client with `base_url` set to the router).
- **Verify:** a completion is returned. Confirms the "AI Router mode" the skill recommends as the
  fastest path is valid against the latest release.
- **Cleanup:** none.

### F2. `@traced` import path is correct
- **Operation:** in a throwaway venv, `pip install orq-ai-sdk`, then attempt the import paths the
  skill documents: `from orq_ai_sdk import traced` and `from orq_ai_sdk.traced import traced`.
- **Verify:** at least one documented path imports successfully; record which. Confirm the **wrong**
  path called out in the skill, `from orq_ai_sdk.tracing import traced`, fails (it should).
- **Drift watch:** if neither documented path imports, report DRIFT (SDK moved `traced`).
- **Cleanup:** delete the throwaway venv. **SKIP** this case if no Python toolchain is available.

### F3. Decorator parameters match the SDK
- **Operation:** introspect `traced` from the installed SDK (signature / help).
- **Verify:** parameters include `name`, `type`, `capture_input`, `capture_output`, `attributes`;
  `user_id` is **not** a direct parameter (belongs in `attributes={}`); `capture_input` /
  `capture_output` default to `True`.
- **Cleanup:** none (reuses F2 venv).

## Behavioural scenarios

### B1. Python OpenAI app → AI Router path
- **Type:** explicit
- **Trigger:** "Add orq.ai tracing to my app"
- **Provide:** a small Python file using `openai.OpenAI()` with no existing tracing.
- **Expected routing:** setup-observability
- **PASS:** scans project, identifies OpenAI SDK, reports no existing tracing; recommends **AI Router**
  mode; changes `base_url` to `https://api.orq.ai/v2/router` using `provider/model` (e.g. `openai/gpt-4o`).
- **Anti-patterns (FAIL):** uses `from orq_ai_sdk.tracing import traced`; hardcodes `service.name=my-app`.

### B2. LangChain app → Observability path
- **Type:** implicit
- **Trigger:** "I want to add tracing but keep my existing LLM calls"
- **Provide:** a Python file using `langchain_openai.ChatOpenAI()` calling a provider directly.
- **Expected routing:** setup-observability
- **PASS:** recommends **Observability** mode; sets OTEL env vars; installs the OpenInference
  instrumentor; initializes the instrumentor **before** framework client creation; warns if any
  `OTEL_*` vars already exist.
- **Anti-patterns (FAIL):** imports the instrumentor after the framework client; ignores existing OTEL config.

### B3. `@traced` usage correctness
- **Type:** explicit
- **Trigger:** "Show me how to use the @traced decorator"
- **Expected routing:** setup-observability
- **PASS:** import path `from orq_ai_sdk.traced import traced` or `from orq_ai_sdk import traced`;
  shows params `name`, `type`, `capture_input`, `capture_output`, `attributes`; documents
  `capture_input`/`capture_output` default `True`.
- **Anti-patterns (FAIL):** shows `user_id` as a direct `@traced` parameter; uses `orq_traced_input()` /
  `orq_traced_output()` (do not exist).

### B4. Sensitive data handling (contextual)
- **Type:** contextual
- **Trigger:** "Add tracing to this function"
- **Provide:** a Python function taking `card_number` and `user_email`.
- **Expected routing:** setup-observability
- **PASS:** uses `capture_input=False` and/or `capture_output=False`; explains defaults are `True`
  (all inputs/outputs sent to orq.ai unless disabled).
- **Anti-patterns (FAIL):** leaves capture on for PII without comment.

### B5. Existing OTEL configuration (contextual)
- **Type:** contextual
- **Trigger:** "Add orq.ai observability"
- **Provide:** a project with `OTEL_EXPORTER_OTLP_ENDPOINT` pointing to Datadog.
- **Expected routing:** setup-observability
- **PASS:** detects existing OTEL config in Phase 1; warns about overwriting; asks for confirmation
  before setting new env vars.
- **Anti-patterns (FAIL):** silently overwrites existing OTEL env vars.

## Negative controls (must NOT fire setup-observability)

### N1. Traces already exist → debugging, not setup
- **Type:** negative
- **Trigger:** "My traces are full of errors — help me figure out what's going wrong."
- **Expected routing:** analyze-trace-failures — setup-observability must not fire.
- **PASS:** routes to analyze-trace-failures (traces already exist; this is debugging).
- **Fired = FAIL:** proposing to (re)install tracing when the user wants to analyze existing traces.
