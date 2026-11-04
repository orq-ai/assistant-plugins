# Model Slugs — which catalogue field names a model

`orq models list -o json` returns a bare list. Each row carries three names for one model, and they are not interchangeable. Correct this rule here once; the skills below cite it.

| Field | What it is | Use it to |
|---|---|---|
| `id` | catalogue UUID, unique | match an agent whose `model.id` is a UUID |
| `refId` | provider-qualified slug the router routes on, unique (`groq/openai/gpt-oss-120b`, `anthropic/claude-haiku-4-5-20251001`) | call, configure or compare a model |
| `model_id` | display alias, **shared across providers** (`openai/gpt-oss-120b` on a groq row; `gpt-4.1` on several rows) | nothing on its own; it addresses the wrong provider path and has returned `403 permission_error` |

## Resolving a model id to one catalogue row

1. **Exact on `id` or `refId`.** Both are unique; a hit is the answer.
2. **Exact on `model_id`, only if exactly one row has it.** Several rows sharing it is ambiguous; say so rather than pick one.
3. **`refId` equal to the id plus a date suffix, only if exactly one row has it.** Agent slugs can omit the version suffix the catalogue carries (`anthropic/claude-haiku-4-5` vs `anthropic/claude-haiku-4-5-20251001`). Accept `-YYYYMMDD` or `-MMDD` only, never a longer name: `openai/gpt-4o` must not resolve to `openai/gpt-4o-mini`.
4. **Otherwise unknown.** Never fall back to a fuzzy match for anything that is called or compared. A list of similar names is fine to show a person.

When turning a slug *into* something to call, prefer `refId` and fall back to `model_id` only when a row has no `refId`.

## Who implements it

| Skill | Where | Steps used |
|---|---|---|
| `orq-review-agent` | `scripts/model_check.py` → `resolve()` | 1–4 |
| `orq-evaluator-alignment` | `lib/orq_client.py` → `_routable_slug()` | slug to call (refId first) |
| `orq-recommend-evaluators` | `resources/create-and-attach.md`, judge model | slug to call (refId first) |
| `orq-cli` | `resources/command-map.md`, `models list` row | projection on `refId` |
