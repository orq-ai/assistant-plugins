# Writing guide

Mechanical rules for the output skill. Consult during Phase 5 alongside [`template.md`](template.md).

## Failure modes to check

Run this checklist against the draft before presenting it.

| Mode | Test | Fix |
|---|---|---|
| **Premature completion** | Does every step have a checkable, exhaustive completion criterion? | Sharpen the criterion |
| **Duplication** | Is any meaning stated in more than one place? | Keep one, delete the rest |
| **No-op** | Does every sentence change behaviour versus the default? | Delete the sentence |
| **Negation** | Are prohibitions phrased as the positive target? | Rewrite as positive; keep "never" only as a hard guardrail, paired with what to do instead |
| **Sprawl** | Is the skill longer than it needs to be? | Disclose reference behind a context pointer |

## Invocation decision

Does this skill need to fire without the user typing its name?

If not, write the description as one human-facing sentence. The agent will only invoke it when the user types its name.

If yes: front-load the leading word in the description and keep one trigger per distinct branch. Include "Use when..." phrasing with the concrete user intents that should route here, plus "Do NOT use when..." for the nearest neighbour skills. See `orq-evaluator-alignment` or `orq-analyze-agent` for examples.

## Completion criteria

Every step in the output skill must end with a checkable done condition. "Every modified model accounted for" beats "produce a change list." A vague criterion invites premature completion.

## Allowed-tools as security boundary

`allowed-tools` controls what the agent can do without prompting. Read/search tools: grant broadly. Write/mutate/delete operations: enumerate individually. `Bash(cli:*)` prefix-matches every destructive subcommand the CLI has. If the skill should never write to the platform, say so in a note above the steps (see `orq-analyze-agent` for the pattern).

## Pruning

Before shipping, run three passes:

1. **Single source of truth**: is any meaning stated in two places? Keep one, delete the other.
2. **Relevance**: does every line bear on what the skill does? Cut what does not.
3. **No-op test**: does each sentence change the agent's behaviour compared to the default? If not, the whole sentence goes.
