# RAG Evaluation Methodology

Specialized methodology for evaluating **Retrieval-Augmented Generation pipelines**. Use alongside the core 5-phase workflow in SKILL.md.

## Contents
- RAG core principle
- Retrieval metrics
- Chunking optimization
- Generation evaluators
- RAGAS evaluators from orq.ai
- RAG failure diagnosis decision tree
- End-to-end RAG evaluation
- Common RAG improvement actions

## RAG Core Principle: Evaluate Retrieval and Generation Separately

**NEVER** rely solely on end-to-end correctness scores. A wrong answer could mean:
- The retriever did not find the right documents (retrieval failure)
- The retriever found the right documents but the LLM ignored/misinterpreted them (generation failure)
- The query was ambiguous and the retriever parsed it incorrectly (query failure)

You **must** separate these to know what to fix.

## Retrieval Metrics

| Metric | Formula | When to Use |
|--------|---------|-------------|
| **Recall@k** | (relevant docs in top k) / (total relevant docs) | **Primary metric.** Missing information is worse than noise -- the LLM can ignore irrelevant chunks but cannot compensate for missing ones. |
| **Precision@k** | (relevant docs in top k) / k | When context window cost or noise matters. |
| **MRR** | 1 / (rank of first relevant doc) | When only one key fact is needed. Less useful for multi-hop questions. |
| **NDCG@k** | Normalized discounted cumulative gain | When chunks have graded relevance (not just binary). Rewards placing better items higher. Critical when context is truncated. |

**Prioritize Recall@k.** For RAG, high recall matters more than high precision because the LLM can ignore irrelevant context but cannot invent missing information.

### Recall Interpretation Table

| Recall@k | Interpretation | Action |
|----------|---------------|--------|
| > 90% | Strong retrieval | Focus on generation quality |
| 70-90% | Adequate but leaky | Improve embeddings, reranking, or query construction |
| < 70% | Retrieval is the bottleneck | Fix chunking, embedding model, or search strategy before evaluating generation |

## Chunking Optimization

Grid search over chunking parameters:
- **Chunk size:** 128, 256, 512, 1024 tokens
- **Overlap:** 0%, 10%, 25%, 50%
- Re-index the corpus for each configuration
- Measure Recall@k and NDCG@k for each

Consider content-aware chunking:
- Use document structure (headings, sections, paragraphs) instead of fixed token counts
- Augment chunks with contextual metadata (document title, section heading)
- For tables: test row-by-row vs column-by-column representations

## Generation Evaluators

Two key metrics for generation quality:

**Answer Faithfulness (grounding):**
- Does the output accurately reflect the retrieved context?
- Check for: hallucinations, omissions, misinterpretations
- Binary check per response: is everything in the answer supported by the retrieved docs?

**Answer Relevance:**
- Does the answer actually address the user's question?
- An answer can be faithful to context but miss the actual question
- Binary check: does the response answer what was asked?

## RAGAS Evaluators from orq.ai Library

Enable these from the Evaluator Library for RAG evaluation:
- **Faithfulness** -- checks if the answer is grounded in retrieved context
- **Context Recall** -- measures how much of the reference answer is supported by retrieved context
- **Context Precision** -- measures signal-to-noise ratio in retrieved context
- **Context Entities Recall** -- checks if key entities from the reference appear in retrieved context
- **Response Relevancy** -- checks if the answer addresses the question
- **Coherence** -- checks if the answer is logically coherent
- **Noise Sensitivity** -- measures robustness to irrelevant retrieved context

These evaluators use `{{log.retrievals}}` automatically.

For custom evaluators, use the `build-evaluator` skill with template variables: `{{log.input}}` (question), `{{log.output}}` (answer), `{{log.retrievals}}` (retrieved chunks), `{{log.reference}}` (ideal answer). One evaluator per criterion (faithfulness, relevance, completeness).

## RAG Failure Diagnosis Decision Tree

```
Question failed. Why?
+-- Retrieved chunks contain the answer?
|   +-- NO -> Retrieval failure. Fix search/chunking/embeddings.
|   +-- YES -> Generation failure. Fix prompt/model.
|              +-- Answer contradicts retrieved context? -> Hallucination
|              +-- Answer ignores retrieved context? -> Context utilization failure
|              +-- Answer is correct but misses the question? -> Relevance failure
```

## End-to-End RAG Evaluation

Run a full experiment combining retrieval + generation:
- Use `create_experiment` with the test dataset
- Attach both retrieval metrics and generation evaluators
- Compare against baseline

Track metrics across runs:

```
| Run | Recall@5 | Faithfulness | Relevance | End-to-End Accuracy | Changes |
|-----|----------|-------------|-----------|---------------------|---------|
| 1   | 72%      | 85%         | 90%       | 65%                 | Baseline |
| 2   | 88%      | 85%         | 90%       | 78%                 | New embedding model |
| 3   | 88%      | 92%         | 93%       | 85%                 | Added grounding instruction |
```

## Common RAG Improvement Actions

| Problem | Metric Signal | Fix |
|---------|--------------|-----|
| Missing relevant docs | Low Recall@k | Better embeddings, query expansion, reranking |
| Too much noise in context | Low Precision@k | Smaller k, reranking, chunk size reduction |
| Answer contradicts sources | Low Faithfulness | Add "only use provided context" instruction |
| Answer ignores the question | Low Relevance | Improve prompt to focus on the question |
| Inconsistent with doc structure | Low on tables/lists | Improve chunking strategy, test representations |
