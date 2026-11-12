---
name: Skill compliance
description: "How completely the agent followed the applicable steps of the selected skill."
labels:
  - value: "ideal"
    description: "Every applicable step followed correctly."
    score: 5
  - value: "good"
    description: "One minor lapse in an applicable step."
    score: 4
  - value: "mixed"
    description: "Material omissions in applicable steps."
    score: 3
  - value: "poor"
    description: "Mostly ineffective use of the procedure."
    score: 2
  - value: "failed"
    description: "The applicable procedure was not followed."
    score: 1
---
**Rubric**

Read the skill in original/ in full, list its steps that applied to this task, then check each against the transcript. Steps that did not apply are not lapses. Use `insufficient_evidence` or `not_applicable` (score null) when the transcript cannot show compliance or the skill was not used. Compliance alone never proves a failed conversation or a skill defect.

**Reason**

One to three sentences naming the steps followed or missed, citing steps.
