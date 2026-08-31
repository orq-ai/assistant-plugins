# orq.ai Evaluator API Reference

Everything `orq-build-evaluator` needs that the MCP server does not cover. Auth is
`Authorization: Bearer $ORQ_API_KEY`, `Content-Type: application/json`, base `https://api.orq.ai`.

## HTTP Fallback for MCP Gaps

MCP covers boolean and numeric LLM/Python evaluators. Everything below has no MCP tool and must go over HTTP (`Authorization: Bearer $ORQ_API_KEY`, `Content-Type: application/json`, base `https://api.orq.ai`) or the CLI.

| Need | Call |
|------|------|
| Create a categorical evaluator | `POST /v2/evaluators` — see below; MCP cannot do this |
| Categorical or string **Python** evaluator | `POST /v2/evaluators` with `type: "python_eval"` — MCP's `create_python_eval` allows only `boolean`/`number` |
| Judge tuning after creation (`model_parameters`, `mode` + `jury`, `repetitions`) | `PATCH /v2/evaluators/{id}` |
| List evaluators | `GET /v2/evaluators?limit=50` — paginate with `starting_after=<last _id>` while `has_more` is true. **Not** `after=` — that parameter is silently ignored and re-serves page 1 forever. |
| Test-run one evaluator | `POST /v3/evaluators/{id}/invoke` — see below |

**Evaluator CRUD is v2 and invoke is v3.** Not a typo to normalise — `/v3/evaluators` 404s and there is no v2 invoke route. Older snippets pointing at `/v2/…/invoke` predate the move.

### Creating a Categorical Evaluator

`categorical_labels` is required at creation. `categories` is the flat mirror of the same label values; send both. `guardrail_config.values` lists the labels that count as passing.

```json
POST /v2/evaluators
{
  "type": "llm_eval",
  "mode": "single",
  "model": "openai/gpt-4.1",
  "prompt": "<the judge prompt, with {{input.user_query}} / {{output.response}}>",
  "output_type": "categorical",
  "key": "tone-classifier",
  "path": "Default",
  "categorical_labels": [
    {"value": "professional", "description": "Formal and courteous"},
    {"value": "casual", "description": "Informal but polite"},
    {"value": "aggressive", "description": "Hostile or rude"}
  ],
  "categories": ["professional", "casual", "aggressive"],
  "guardrail_config": {"type": "categorical", "values": ["professional", "casual"], "enabled": true, "alert_on_failure": false}
}
```

The response echoes `categories` and `categorical_labels` but returns `output_type: null` — read the label fields, not `output_type`, to confirm the evaluator is categorical.

The same call via the CLI, which is the better option in a script or CI job (`orq` handles auth and workspace selection):

```bash
orq evals create --json \
  --key tone-classifier --type llm_eval --mode single --model openai/gpt-4.1 \
  --output-type categorical --path Default \
  --prompt "$(cat judge-prompt.txt)" \
  --categories professional --categories casual --categories aggressive \
  --categorical-labels '[{"value":"professional","description":"Formal and courteous"},{"value":"casual","description":"Informal but polite"},{"value":"aggressive","description":"Hostile or rude"}]' \
  --guardrail-config '{"type":"categorical","values":["professional","casual"],"enabled":true,"alert_on_failure":false}'
```

`--categories` is repeatable; `--categorical-labels` and `--guardrail-config` take JSON strings. See the `orq-cli` skill for auth and profile selection.

### Programmatic Invoke

`POST /v3/evaluators/{id}/invoke` runs one evaluator against a single input/output pair — use it for rapid Phase 5 iteration instead of a full experiment. Or `orq evals invoke <id> --json --query ... --output ...`, which calls the same endpoint.

Send the run under `context`. **`context` is the request envelope, not a namespace you can reference from the prompt** — there is no `{{context.…}}` variable; writing one renders empty. It is the wrapper whose contents feed the variables:

```json
{
  "context": {
    "input": {"user_query": "...", "system_instructions": "...", "retrievals": ["..."], "expected_output": "..."},
    "output": {"response": "...", "tools_called": [{"name": "...", "arguments": "...", "output": "..."}]},
    "messages": [{"role": "user", "content": "..."}],
    "variables": {"my_custom_var": "..."}
  }
}
```

| Body path | Renders as |
|-----------|-----------|
| `context.input.<field>` | `{{input.<field>}}` — name matches |
| `context.output.<field>` | `{{output.<field>}}` — name matches |
| `context.messages` | `{{input.all_messages}}` — **name does not match** |
| `context.variables.<name>` | `{{<name>}}` — **the `variables.` prefix is dropped**; `{{variables.<name>}}` renders empty |

A flat shorthand is still accepted and folds into `context`: `query` → `input.user_query`, `output` → `output.response`, `reference` → `input.expected_output`, and `messages`/`retrievals`/`variables` keep their top-level names. **There is no flat alias for `system_instructions` or `tools_called` — those two variables are reachable only through `context`.**

The CLI mirrors this. `--query`/`--output`/`--reference`/`--retrievals`/`--messages` fold into `context` the same way, `--variables` takes repeatable `key=value`, and there is no `--system-instructions` or `--tools-called` flag — reach those through `--context`. Note which level each flag expects:

```bash
# --context takes the INNER object (no "context" key)
orq evals invoke <id> --json \
  --context '{"input":{"system_instructions":"...","user_query":"..."},"output":{"response":"..."}}'

# --from-file takes the FULL body (with "context")
orq evals invoke <id> --json --from-file body.json   # {"context":{"input":{...}}}
```

**Unknown fields are ignored silently — no error, the variable renders empty and the judge scores a prompt with a hole in it.** `input` and `expected_output` are not flat aliases (they exist only inside `context.input`), so `{"input": …}` at the top level is dropped. When `messages` is present it is the conversation and `input.user_query` is ignored; `output.response` is appended only if the conversation has no assistant turn.

Returns `{"type", "value", "evaluator_id", "status", "passed", "explanation", "categories"}`, and optionally `trace_id`, `span_id`, `confidence` — `value` is the verdict (`true`/`false`, a number, or the chosen label). A categorical evaluator reports `type: "string"`, not `"categorical"`. `passed` is the guardrail's decision when the evaluator has one and the grader's own judgement otherwise, so read `guardrail_config` to tell which. The `id` also accepts `id@version` or `id@environment` to grade against a published version.

> Verify a new judge sees what you think it sees: invoke it twice with the deciding fact moved in and out of one field. If the verdict does not change, that field is not reaching the prompt.
