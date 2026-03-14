<skills>

You have additional SKILLs documented in directories containing a "SKILL.md" file.

These skills are:
 - analyze-trace-failures -> "skills/analyze-trace-failures/SKILL.md"
 - build-agent -> "skills/build-agent/SKILL.md"
 - build-evaluator -> "skills/build-evaluator/SKILL.md"
 - generate-synthetic-dataset -> "skills/generate-synthetic-dataset/SKILL.md"
 - optimize-prompt -> "skills/optimize-prompt/SKILL.md"
- run-experiment -> "skills/run-experiment/SKILL.md"

IMPORTANT: You MUST read the SKILL.md file whenever the description of the skills matches the user intent, or may help accomplish their task.

<available_skills>

build-agent: `Design, create, and configure an orq.ai Agent with tools, instructions, knowledge bases, and memory — includes model selection, KB management, and memory store setup`

build-evaluator: `Create validated LLM-as-a-Judge evaluators following best practices — binary Pass/Fail judges with TPR/TNR validation for measuring specific failure modes`

analyze-trace-failures: `Read production traces, identify what's failing, build failure taxonomies, and categorize issues using open coding and axial coding methodology`

run-experiment: `Create and run orq.ai experiments — compare configurations against datasets using evaluators, analyze results, with specialized methodology for agent, conversation, and RAG evaluation`

generate-synthetic-dataset: `Generate and curate evaluation datasets — structured generation, quick from description, expansion from existing data, plus dataset maintenance and quality improvement`

optimize-prompt: `Analyze and optimize system prompts using a structured prompting guidelines framework — AI-powered analysis and rewriting`

</available_skills>

Paths referenced within SKILL folders are relative to that SKILL. For example the build-evaluator `resources/judge-prompt-template.md` would be referenced as `skills/build-evaluator/resources/judge-prompt-template.md`.

</skills>

<commands>

You have slash commands available in the `commands/` directory:

 - quickstart -> "commands/quickstart.md" — Interactive onboarding guide for new users
 - invoke -> "commands/invoke.md" — Invoke an orq.ai agent or deployment and display the response
 - workspace -> "commands/workspace.md" — Show workspace overview (agents, deployments, prompts, datasets, experiments)
 - traces -> "commands/traces.md" — Query and summarize traces with filters (debugging entry point)
 - analytics -> "commands/analytics.md" — Performance metrics (latency, cost, tokens, error rates)
 - models -> "commands/models.md" — List available AI models and capabilities

Commands are quick-action tools. Skills are multi-step workflows. Use `/orq:quickstart` for onboarding, `/orq:invoke`, `/orq:workspace`, `/orq:traces`, `/orq:analytics`, or `/orq:models` for fast operations. Use skills when the user needs guided, multi-step work.

</commands>
