<skills>

You have additional SKILLs documented in directories containing a "SKILL.md" file.

These skills are:
 - action-plan -> "skills/action-plan/SKILL.md"
 - annotation-workflow -> "skills/annotation-workflow/SKILL.md"
 - audit-evaluation-pipeline -> "skills/audit-evaluation-pipeline/SKILL.md"
 - build-agent -> "skills/build-agent/SKILL.md"
 - build-evaluator -> "skills/build-evaluator/SKILL.md"
 - curate-dataset -> "skills/curate-dataset/SKILL.md"
 - evaluate-agent -> "skills/evaluate-agent/SKILL.md"
 - evaluate-conversation -> "skills/evaluate-conversation/SKILL.md"
 - evaluate-rag -> "skills/evaluate-rag/SKILL.md"
 - feedback-loop -> "skills/feedback-loop/SKILL.md"
 - generate-synthetic-dataset -> "skills/generate-synthetic-dataset/SKILL.md"
 - knowledge-base -> "skills/knowledge-base/SKILL.md"
 - list-models -> "skills/list-models/SKILL.md"
 - manage-deployment -> "skills/manage-deployment/SKILL.md"
 - manage-memory -> "skills/manage-memory/SKILL.md"
 - monitor-production -> "skills/monitor-production/SKILL.md"
 - optimize-prompt -> "skills/optimize-prompt/SKILL.md"
 - regression-test -> "skills/regression-test/SKILL.md"
 - run-experiment -> "skills/run-experiment/SKILL.md"
 - scaffold-integration -> "skills/scaffold-integration/SKILL.md"
 - setup-ci-eval -> "skills/setup-ci-eval/SKILL.md"
 - prompt-optimizer -> "skills/prompt-optimizer/SKILL.md"
 - trace-analysis -> "skills/trace-analysis/SKILL.md"

IMPORTANT: You MUST read the SKILL.md file whenever the description of the skills matches the user intent, or may help accomplish their task.

<available_skills>

action-plan: `Generate a prioritized action plan from LLM experiment results following the Analyze-Measure-Improve lifecycle`
annotation-workflow: `Set up human annotation queues for evaluator validation, labeling, and quality assurance`
audit-evaluation-pipeline: `Audit an existing LLM eval pipeline and produce a prioritized diagnostic report with concrete next steps`
build-agent: `Design, create, and configure an orq.ai Agent with tools, instructions, knowledge bases, and memory`
build-evaluator: `Create validated LLM-as-a-Judge evaluators following evaluation best practices`
curate-dataset: `Clean, deduplicate, balance, and augment existing evaluation datasets for higher quality experiments`
evaluate-agent: `Evaluate agentic and tool-calling LLM pipelines with stage-specific graders and transition failure matrices`
evaluate-conversation: `Evaluate multi-turn conversation quality with session-level, turn-level, and memory/coherence checks`
evaluate-rag: `Evaluate RAG pipelines by separately measuring retrieval quality and generation faithfulness`
feedback-loop: `Set up and analyze user feedback collection to drive data-informed improvement cycles`
generate-synthetic-dataset: `Generate structured, diverse evaluation datasets — from scratch using dimensions-tuples methodology, from a description, or by expanding existing datasets`
knowledge-base: `Manage orq.ai Knowledge Bases — create, configure, upload files, chunk content, search, and maintain RAG data sources via API`
list-models: `List available models and systematically compare them on cost, latency, and quality to find the optimal choice for your use case`
manage-deployment: `Configure, version, and manage orq.ai deployments — model selection, prompt linking, fallbacks, caching, and guardrails`
manage-memory: `Create and configure orq.ai Memory Stores for persistent context in conversational agents`
monitor-production: `Analyze production trace data for anomalies, cost trends, latency regressions, and emerging failure modes`
optimize-prompt: `Systematically iterate on a prompt deployment using trace data, A/B testing, and structured refinement techniques`
regression-test: `Run a quick regression check against a golden dataset to verify recent changes haven't degraded quality`
run-experiment: `End-to-end LLM evaluation workflow — error analysis, dataset creation, experiment execution, result analysis, and ticket filing`
scaffold-integration: `Generate SDK integration code (Python or Node) for orq.ai agents, deployments, and knowledge bases in the user's codebase`
setup-ci-eval: `Set up continuous evaluation in CI/CD pipelines that runs regression tests on every prompt or agent change`
prompt-optimizer: `Analyze and optimize system prompts — get AI-powered analysis and rewriting using structured prompting guidelines`
trace-analysis: `Systematic trace reading and failure taxonomy building using open coding and axial coding`
</available_skills>

Paths referenced within SKILL folders are relative to that SKILL. For example the build-evaluator `resources/judge-prompt-template.md` would be referenced as `skills/build-evaluator/resources/judge-prompt-template.md`.

</skills>
