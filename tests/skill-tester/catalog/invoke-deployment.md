# Catalog: invoke-deployment

Tests for [`skills/invoke-deployment/SKILL.md`](../../../skills/invoke-deployment/SKILL.md).
Source scenarios: [`../../skills.md`](../../skills.md) (invoke-deployment).

## Functional cases

### F1. Invoke the seeded agent (responses API)
- **Operation:** invoke the seeded `orq-skills-test-echo` agent via the agent responses API
  (`invoke_agent`, model `agent/orq-skills-test-echo`), sending a short message.
- **Verify:** a response is returned and the echo agent reflects the input back. Confirms the
  agent-invocation path the skill generates code for actually works.
- **Cleanup:** none beyond the shared seeded agent.

### F2. AI Router model call (`provider/model`)
- **Operation:** call a model directly through the AI Router (`invoke_model` with `openai/gpt-4.1-mini`,
  or an OpenAI client pointed at `base_url="https://api.orq.ai/v2/router"`).
- **Verify:** a completion is returned. Confirms the `provider/model` format and `/v2/router` endpoint
  the skill documents are valid against the latest release.
- **Cleanup:** none.

### F3. Resolve a deployment key
- **Operation:** `search_entities(type=deployment)`.
- **Verify:** call succeeds and returns the deployment list used to confirm a key before codegen
  (Constraint: "confirm the key with `search_entities` before writing code").
- **Drift watch:** `list_registry_keys` is referenced in
  `skills/invoke-deployment/resources/api-reference.md:20` but is not exposed by the server. Report
  as DRIFT if unavailable.
- **Cleanup:** none (read-only).

## Behavioural scenarios

### B1. Deployment happy path
- **Type:** explicit
- **Trigger:** "Invoke my deployment `customer-support` with variable `customer_name` set to 'Jane'"
- **Expected routing:** invoke-deployment
- **PASS:** confirms the key via `search_entities(type=deployment)`; maps `{{customer_name}}` as a
  required input; generates Python using `client.deployments.invoke(key=..., inputs={...})`; reads
  the key from `os.environ["ORQ_API_KEY"]`; includes `identity={"id": ...}`.
- **Anti-patterns (FAIL):** hardcodes the API key; omits the variable mapping; skips identity.

### B2. Agent multi-turn
- **Type:** explicit
- **Trigger:** "Send a message to my agent and then follow up"
- **Expected routing:** invoke-deployment
- **PASS:** uses `client.agents.responses.create()`; A2A `parts: [{kind: "text", text: ...}]` message
  format; saves `task_id` and passes it in the follow-up.
- **Anti-patterns (FAIL):** uses `client.agents.invoke()`; OpenAI-style `content` instead of A2A
  parts; drops `task_id`.

### B3. Model via AI Router
- **Type:** explicit
- **Trigger:** "Call GPT-4.1 directly through the AI Router"
- **Expected routing:** invoke-deployment
- **PASS:** `provider/model` format (e.g. `openai/gpt-4.1`); points OpenAI client at
  `base_url="https://api.orq.ai/v2/router"`; uses `openai.OpenAI()`, not the orq SDK, for this path.
- **Anti-patterns (FAIL):** bare model name; uses the orq SDK for a raw router call.

### B4. Streaming recommendation (contextual)
- **Type:** contextual
- **Trigger:** "I'm wiring up a customer-facing chatbot UI and need to call my deployment to power the replies."
- **Expected routing:** invoke-deployment
- **PASS:** recommends `stream=True` for user-facing invocations; generates an invocation against the
  deployment despite the surrounding product context.
- **Anti-patterns (FAIL):** ignores streaming for an interactive UI; gets distracted into UI/build advice
  instead of the invocation.

### B5. Implicit — a need, not the artifact
- **Type:** implicit
- **Trigger:** "I set up a prompt in orq.ai — how do I get my Python app to actually run it and get responses back?"
- **Expected routing:** invoke-deployment
- **PASS:** routes here on the description; confirms the key via `search_entities`; generates SDK
  invocation code reading `ORQ_API_KEY` from the environment and including `identity`.
- **Anti-patterns (FAIL):** misroutes to build-agent / optimize-prompt; hardcodes the key.

## Negative controls (must NOT fire invoke-deployment)

### N1. Creating, not invoking
- **Type:** negative
- **Trigger:** "Help me improve the system prompt on my `customer-support` deployment."
- **Expected routing:** optimize-prompt — invoke-deployment must not fire.
- **PASS:** routes to optimize-prompt (editing the prompt), not to generating an invocation call.
- **Fired = FAIL:** generating SDK invocation code when the user wants to *edit* the deployment.
