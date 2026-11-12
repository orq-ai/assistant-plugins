---
name: Efficiency
description: "How efficiently the agent used this skill: waste attributable to loading or applying it."
labels:
  - value: "highly_efficient"
    description: "Loading and applying the skill cost nothing it did not need: no redundant steps, no rework, no correction tied to it."
    score: 1
  - value: "mostly_efficient"
    description: "One or two small slips tied to the skill (a duplicated read, an early retry) with no knock-on cost."
    score: 0.8
  - value: "mostly_inefficient"
    description: "Repeated waste tied to the skill, or a round of rework or user correction its use caused."
    score: 0.4
  - value: "highly_inefficient"
    description: "Skill-related waste dominated the run: the same step reworked across cycles, repeated user correction, or looping."
    score: 0.2
---
**Rubric**

Score only the cost of loading and applying the selected skill, not the whole session. Count redundant steps, rework and avoidable user correction that trace back to how the skill was loaded or followed. Steps the skill itself prescribes are not waste even if they look costly; name them in the reason so a skill edit can address them. Use the code-counted stats in the transcript header, not invented counts.

Use `insufficient_evidence` (score null) when the transcript does not show enough of the skill's use, and `not_applicable` (score null) when the skill was not used.

**Reason**

One to three sentences naming the dominant waste with a rough count and its cause: the agent, or an instruction in the skill.
