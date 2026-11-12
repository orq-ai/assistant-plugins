---
name: Quality
description: "Quality of the skill's usage: right moment, applied with judgement, output actually used."
labels:
  - value: "excellent"
    description: "Loaded when the task called for it, adapted to this task, and its output was reported, acted on, or rejected with a reason."
    score: 1
  - value: "good"
    description: "Sound use with a minor lapse: slightly late, one step applied mechanically, or output used only in part."
    score: 0.8
  - value: "weak"
    description: "Material lapse: loaded at the wrong moment, applied by rote where the task needed judgement, or output mostly dropped."
    score: 0.4
  - value: "poor"
    description: "The skill's use added little: wrong task, ignored output, or results contradicted without a reason."
    score: 0.2
---
**Rubric**

Judge the usage of the selected skill, not the quality of code or of any artifact the agent was reviewing. Ask three questions: was it loaded at the right moment for the task; was it applied with judgement to this task rather than by rote; was its output used (reported to the user, acted on, or explicitly rejected with a reason)?

Use `insufficient_evidence` (score null) when the transcript does not show the outcome of the skill's use, and `not_applicable` (score null) when the skill was not used.

**Reason**

One to three sentences quoting or closely paraphrasing the step that drove the grade.
