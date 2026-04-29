# Anti-Patterns to Prevent

Common mistakes when running experiments, organized by evaluation type.

## Contents
- General anti-patterns
- Agent-specific anti-patterns
- Conversation-specific anti-patterns
- RAG-specific anti-patterns

## General Anti-Patterns

| Anti-Pattern | What to Do Instead |
|---|---|
| Running experiments without a dataset | Always create a structured dataset first |
| Using generic "helpfulness" or "quality" evaluators | Build application-specific criteria from error analysis |
| One giant evaluator with 5+ criteria bundled | Separate evaluators per failure mode |
| Re-running experiments without fixing anything | File tickets, make changes, THEN re-run |
| Ignoring low scores on "easy" questions | Simple questions often reveal the most important failures |
| Skipping adversarial test cases | Always include jailbreak/persona-breaking/off-topic scenarios |
| Treating eval as a one-time setup | It is an ongoing loop -- Analyze -> Measure -> Improve -> repeat |
| Jumping to model upgrade after low scores | Fix prompt first, upgrade model last |
| Creating one mega-ticket for all improvements | One ticket per fix, each with clear success criteria |
| Ignoring false fails from the evaluator | Inspect judge reasoning; fix the evaluator if it is wrong |
| Re-running experiment without changing anything | Always make a specific, documented change before re-running |
| Optimizing for 100% pass rate | Target 70-85%; expand coverage if passing too easily |
| Building evaluators for specification failures | Fix the prompt first; only automate for generalization failures |
| Fixing failures without updating the dataset | Add test cases for every failure you fix |

## Agent-Specific Anti-Patterns

| Anti-Pattern | What to Do Instead |
|---|---|
| Checking exact tool call sequences | Grade outcomes, not paths -- agents find valid alternative approaches |
| One grader for everything | Use isolated grader per dimension |
| No agency level definition | Define expected agency level (high/low/mixed) first |
| Only evaluating end-to-end | Evaluate 4 tool-calling stages independently |
| Single trial per task | Run 3-5 trials, use pass@k / pass^k |
| Testing only happy paths | Include adversarial, edge case, and escalation tasks |
| Over-relying on final outcome | Inspect the transition failure matrix for intermediate failures |
| Shared state between trials | Isolate each trial in a clean environment |

## Conversation-Specific Anti-Patterns

| Anti-Pattern | What to Do Instead |
|---|---|
| Evaluating every turn in every conversation | Start at session level, drill into turns only for failures |
| Building multi-turn tests for single-turn failures | Try reproducing as single-turn first |
| Only testing short conversations (2-3 turns) | Test with 5, 10, 20+ turn conversations |
| Using one LLM to both simulate and evaluate | Separate simulation model from judge model |
| No perturbation testing | Include goal changes, corrections, contradictions |
| Treating all turns equally | Focus analysis on the first failing turn |
| Ignoring memory store behavior | Explicitly test cross-session recall if using memory |

## RAG-Specific Anti-Patterns

| Anti-Pattern | What to Do Instead |
|---|---|
| Only measuring end-to-end accuracy | Evaluate retrieval and generation separately |
| Skipping retrieval evaluation | Always measure Recall@k first |
| Using only BLEU/ROUGE for generation | Use faithfulness + relevance evaluators |
| Fixed chunk size for all content | Use content-aware chunking where possible |
| Overfitting to synthetic eval dataset | Mix synthetic with manually curated questions |
| Choosing MRR when you need Recall | Use Recall@k for multi-hop, MRR for single-fact |
| Ignoring chunking as a tunable parameter | Grid search over chunk size and overlap |
| Feeding full documents to the LLM judge | Give the judge only the relevant chunk + question + answer |
