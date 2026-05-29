# Synthetic Data Generation: Do's and Don'ts

Operational rules distilled from the literature on synthetic data generation for LLM pipelines. The underlying research covers eval datasets, red-team / adversarial sets, and multi-turn agent trajectories.

Three headline rules:
1. **Accumulate, never replace.** Synthetic data is additive. Replace-style loops are the canonical model-collapse setup (Gerstgrasser et al., 2024).
2. **The filter is the product.** Generate 3–5× what you keep. Quality lives in the filter stack, not in the generation prompt.
3. **Generator diversity is the jury argument, re-applied.** Mix providers for independent error surfaces. Single-source synthetic data weakens fine-tuning's de-biasing effect (Synthetic Eggs in Many Baskets, ACL Findings 2026).

---

## The Full Table

| Decision | Do | Don't |
|---|---|---|
| **Real vs synthetic mix** | Accumulate synthetic on top of real data. Keep real data in every iteration. | Replace real data with synthetic. This is the canonical model-collapse setup. |
| **Generator choice** | Use 2–3 generators from different providers (Anthropic + OpenAI + open-weights). | Train on a single generator. Style and bias inherit. |
| **Generator vs judge separation** | Use different model families for generation and for filtering/judging. | Filter Claude-generated data with Claude. Self-preference bias inflates pass rates. |
| **Volume strategy** | Generate 3–5× what you plan to keep. Aggressive filter. | Generate exactly what you need. No headroom for filtering means low-quality data ships. |
| **Verification** | Use mechanical verifiers (run code, check math, parse schema) wherever the task allows. | Trust the generator's self-reported correctness. It is consistently overconfident. |
| **Diversity** | Persona-condition, prompt-evolve, or seed from real user inputs. Measure embedding spread. | Generate 50K examples from one prompt template. The set collapses to a few modes. |
| **Deduplication** | Embed and dedupe with cosine threshold 0.85–0.92 **before** train/eval split. | Dedupe after splitting. You'll leak train examples into eval. |
| **Eval data** | Hold out a real eval set the generator has never seen. Decontaminate semantically. | Generate synthetic eval from the same pipeline as synthetic train. You'll measure memorization, not quality. |
| **Bias control** | Audit for stereotype amplification on a labeled probe set. | Assume synthetic data is bias-neutral. Generators carry training-data bias, and fine-tuning amplifies it. |
| **Hallucination** | Filter on factuality with a verifier or jury. Pair every example with grounding when possible. | Ship unverified generations into fine-tuning. Hallucination amplification under unverified pipelines is widely reported. |
| **Provenance** | Log generator model, version, prompt, temperature, and timestamp per example. | Lose track of which model produced which row. You will need this when something breaks. |
| **Iteration loop** | Iterate: generate → filter → eval → inspect failures → update prompts. Expect 2–3 cycles. | Generate once, ship, hope. The first run reveals your real failure modes. |
| **Format compliance** | Validate schema and structure before semantic checks. Cheapest filter first. | Run expensive LLM-judge filtering on data that fails basic format checks. |
| **Domain coverage** | Audit topic distribution against your target use case. Fill gaps with targeted generation. | Generate uniformly and assume coverage matches usage. It rarely does. |

---

## Filter Stack Order

Run filters cheapest-first:

1. **Format / parseability** — schema, regex, length bounds. Discard immediately.
2. **Mechanical verifier** (where task allows) — run code, check math, parse output against schema. Highest-signal filter you can have.
3. **LLM-as-judge** — 3-model panel, cross-provider (not the same family as the generator). See `build-evaluator` for jury setup.
4. **Deduplication** — embed + cosine. Working threshold: 0.85 (aggressive) to 0.92 (permissive). Tune per embedding model and domain.
5. **Diversity scoring** — greedy quality-diversity optimization when you have more candidates than budget.

**Keep rate heuristic:** if you're keeping >50% of generated examples, your filter is too loose. If <10%, your generation prompt is wrong.

---

## Use-case specific guidance

### Eval dataset generation

- Seed from real production traces (e.g., from orq.ai tracing), not from a blank prompt. The eval distribution should mirror the production distribution.
- Generate contrastive pairs and edge cases, not just happy-path examples: clearly good vs clearly bad vs borderline.
- Decontaminate semantically against your held-out real eval set. N-gram dedup is insufficient — rephrased test items bypass it (Yang et al., 2023).
- Generate with one provider; judge with another (self-preference bias).
- Synthetic eval is good for **coverage** (finding failure modes), bad for **absolute scoring** (numbers drift toward the generator's prior). Use synthetic to find bugs; use real held-out eval to measure progress.

### Red-team / adversarial generation

- Condition on attacker archetypes (curious user, jailbreaker, social engineer, prompt-injection author, regulated-domain abuser). Each archetype hits a different attack surface.
- Start from a harm taxonomy before generating — external anchors: [OWASP LLM Top 10 (2025)](https://owasp.org/www-project-top-10-for-large-language-model-applications/), [NIST AI RMF GenAI Profile (AI 600-1)](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf), [MLCommons AILuminate v1.0](https://mlcommons.org/benchmarks/ailuminate/). Measure coverage per category before measuring pass-rate.
- Apply Evol-Instruct in reverse: start from a known-bad prompt the model now refuses, evolve it toward subtlety (rephrase, add context, embed in a benign frame).
- Always judge with a separate-family model. A red-team set is worthless if the safety classifier was trained by the same lab that wrote the generator.
- See the `evaluatorq` skill (`eq redteam`) for automated adversarial red teaming.

### Multi-turn agent trajectories

- Use a user-simulator + agent-rollout setup: one LLM plays the user (with a goal and persona), the other is the agent under test. Log the trajectory; label from goal-completion signal.
- Generate tool-use trajectories from a tool schema and a goal, then verify mechanically (did args parse, did the call succeed). The verifier is much stronger than for prose.
- Blueprint-first, then rollout (APIGen-MT pattern): a committee of reviewers builds a verified task blueprint, then realizes it through simulated agent–human interplay.
- Watch for trajectory monoculture: vary persona, goal complexity, and turn budget the same way you'd vary seed prompts.
- Don't synthesize the user and the success label with the same model — that's self-grading. Pull the success signal from the tool layer or from a separate judge.
- Economics: a single agent trajectory can cost as much as 50 single-turn pairs. Push toward smaller, more curated synthetic sets and harder filtering.

---

## When NOT to use synthetic data

- **Real data exists and is accessible.** Synthetic is a substitute for missing real data. If you have real data, use it.
- **For final evaluation.** Synthetic eval measures consistency with the generator, not real-world performance. Always hold out a real eval set.
- **For modeling rare humans.** User-behavior models, recommendation systems, demographic studies need real distributions. Generators sample from training-data distributions, not human ones.
- **When you cannot verify outputs.** If no mechanical or judge-based verifier exists, synthetic data risk is hard to bound. Build the verifier first.
- **For high-stakes domains without expert review.** Medical, legal, financial. Generator confidence is uncalibrated. Human review is non-optional.

---

## Key reference numbers

| Number | What | Source |
|---|---|---|
| 3–5× | Generation-to-kept ratio in practitioner pipelines | Industry practitioner playbooks |
| 0.85–0.92 | Cosine threshold for embedding deduplication (tune per model) | Practitioner heuristic |
| 20–30% | Target keep rate after full filter stack | Practitioner heuristic |
| ~40% | Synthetic share of Phi-4 pretraining data | Phi-4 Tech Report (Abdin et al., 2024) |
| ~33% | Synthetic share of Nemotron 3 Nano pretraining | NVIDIA Nemotron 3 Nano (Dec 2025) |
| 1.5M | Tool-agentic trajectories in TOUCAN (2,000 MCP tools) | Xu et al. (2025) |
| 4.7× | Hallucination amplification for unverified synthetic data (preliminary, single paper — treat as directional) | Silva-Atencio (2025) |

---

*Based on internal research artifact (RES-774, v0.4), covering: Shumailov et al. (Nature 2024), Gerstgrasser et al. (2024), Self-Instruct, Evol-Instruct, Persona Hub, Magpie, Phi-4, Li et al. (2025), Yang et al. (2023), TOUCAN, APIGen-MT (NeurIPS 2025), Synthetic Eggs in Many Baskets (ACL Findings 2026).*
