# Decisions API contract

Contract checked against orquesta-web `main` at
[`b94898e56f69bcbd4cb11bd8a555d23ca7bd358c`](https://github.com/orq-ai/orquesta-web/tree/b94898e56f69bcbd4cb11bd8a555d23ca7bd358c) on 2026-10-22.
This is source evidence; no paid live `label` run was performed in this review.

- [`publicopenapi/contract.go`](https://github.com/orq-ai/orquesta-web/blob/b94898e56f69bcbd4cb11bd8a555d23ca7bd358c/apps/responses-api/publicopenapi/contract.go) registers `POST /v3/router/decisions` and deprecates `/v3/router/classify` with the same contract.
- [`types_classify.go`](https://github.com/orq-ai/orquesta-web/blob/b94898e56f69bcbd4cb11bd8a555d23ca7bd358c/apps/responses-api/publicopenapi/types_classify.go) defines request `model`, JSON `state`, and keyed `questions`; response `answers` keyed by question; `noul` probability, `choice` label and `refusal` variants; optional `usage.total_cost` in USD.
- The [Decisions guide](https://github.com/orq-ai/orquesta-web/blob/b94898e56f69bcbd4cb11bd8a555d23ca7bd358c/documentation/mintlify/ai-gateway/features/decisions.mdx) uses `typesafe/jev-latest`; the request schema explicitly supports `openai/gpt-6-luna`. The API key requires `classify` permission in addition to access to the traces being read.
- Input-price estimates use the [published model catalogue figures](https://orq.ai/blog/decider-4b-a-sovereign-decision-model-on-orq): Jev $0.042, Decider $0.035, GPT-6 Luna $0.10 per million input tokens. These are estimates, not billing guarantees.

Synthetic response matching those schema fields:

```json
{"model":"typesafe/jev-latest","answers":{"failed":{"type":"noul","noul":0.9}},"usage":{"input_tokens":100,"output_tokens":0,"total_cost":0.0000042}}
```

`tests/test_decide.py` checks the wire path and parsing with an HTTP mock, plus unknown/refused answers, missing costs, retryable labels and authorization failures. Reproduce offline:

```bash
uv run --no-project --with-requirements skills/orq-analyze-traces/tests/requirements.txt \
  python -m pytest skills/orq-analyze-traces/tests/ -q
```

To check a configured deployment, run `uv run skills/orq-analyze-traces/scripts/decide.py probe`
with `ORQ_API_KEY` set; it sends synthetic content and incurs a small charge. A successful probe
confirms model availability and answer readability, not full-window labelling quality.
