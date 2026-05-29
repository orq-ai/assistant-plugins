# Persona and Scenario Templates

Filled examples for `evaluatorq.simulation.types.Persona` and `Scenario`. The
runner generates one datapoint per `(persona, scenario)` pair and the
`UserSimulatorAgent` reads the scalars to decide tone and behavior.

## Persona

```python
from evaluatorq.simulation import (
    Persona,
    CommunicationStyle,
    EmotionalArc,
    CulturalContext,
)

skeptical_founder = Persona(
    name="skeptical-founder",
    patience=0.3,
    assertiveness=0.8,
    politeness=0.5,
    technical_level=0.7,
    communication_style=CommunicationStyle.terse,
    background=(
        "Solo founder of a 2-person SaaS company evaluating customer-support "
        "tooling for the first time. Has been burned by two previous vendors "
        "and is allergic to vague answers."
    ),
    emotional_arc=EmotionalArc.escalating,
    cultural_context=CulturalContext.direct,
)
```

### Field reference

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Stable handle, used in trace metadata |
| `patience` | `float [0-1]` | 0 = explodes at the first delay, 1 = endless wait |
| `assertiveness` | `float [0-1]` | 0 = defers, 1 = pushes for outcomes |
| `politeness` | `float [0-1]` | 0 = rude, 1 = formal courtesy |
| `technical_level` | `float [0-1]` | 0 = non-technical, 1 = power user |
| `communication_style` | `CommunicationStyle` | `formal`, `casual`, `terse`, `verbose` |
| `background` | `str` | One-paragraph who-they-are |
| `emotional_arc` | `EmotionalArc?` | `stable`, `escalating`, `de_escalating`, `volatile`, `manipulative`, `hostile` |
| `cultural_context` | `CulturalContext?` | `neutral`, `direct`, `indirect`, `high_context`, `low_context`, `hierarchical` |

### Diversity for multi-persona runs

Vary along the scalar axes rather than freeform `background`. The simulator
reads scalars deterministically; freeform text drifts.

Pick 2–3 scalars per run and generate the grid:

```python
import itertools
from evaluatorq.simulation import Persona, CommunicationStyle

personas = [
    Persona(
        name=f"p-tech{tl:.1f}-pat{pat:.1f}",
        patience=pat,
        assertiveness=0.5,
        politeness=0.5,
        technical_level=tl,
        communication_style=CommunicationStyle.casual,
        background="Long-time customer evaluating a new feature.",
    )
    for tl, pat in itertools.product([0.2, 0.5, 0.8], [0.2, 0.5, 0.8])
]
```

Or skip this entirely and let `PersonaGenerator` synthesize from an agent
description, see `generate_and_simulate()` in
[simulation-loop.md](simulation-loop.md).

## Scenario

```python
from evaluatorq.simulation import (
    Scenario,
    Criterion,
    StartingEmotion,
    ConversationStrategy,
    InputFormat,
)

refund_digital = Scenario(
    name="refund-digital-download",
    goal=(
        "Get a refund for a digital download purchased 4 days ago. "
        "The download link expired before the user could use it."
    ),
    context="Order #4421, paid by card, no chargeback initiated.",
    starting_emotion=StartingEmotion.frustrated,
    criteria=[
        Criterion(
            description="Agent confirms refund eligibility within 3 turns",
            type="must_happen",
        ),
        Criterion(
            description="Agent asks for proof-of-purchase before issuing refund",
            type="must_happen",
        ),
        Criterion(
            description="Agent promises a refund without verifying the order",
            type="must_not_happen",
        ),
    ],
    is_edge_case=False,
    conversation_strategy=ConversationStrategy.cooperative,
    input_format=InputFormat.plain_text,
)
```

### Field reference

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Stable handle |
| `goal` | `str` | What the simulated user is trying to achieve |
| `context` | `str?` | Background the user has in mind |
| `starting_emotion` | `StartingEmotion?` | `neutral`, `frustrated`, `confused`, `happy`, `urgent` |
| `criteria` | `list[Criterion]?` | Each: `{description, type: must_happen \| must_not_happen}` |
| `is_edge_case` | `bool?` | Marks edge-case scenarios for analysis |
| `conversation_strategy` | `ConversationStrategy?` | `cooperative`, `topic_switching`, `contradictory`, `multi_intent`, `evasive`, `repetitive`, `ambiguous` |
| `ground_truth` | `str?` | What the correct outcome looks like (read by the judge) |
| `input_format` | `InputFormat?` | `plain_text`, `with_url`, `with_attachment`, `form_data`, `code_block`, `mixed_media` |

### Criteria drive the judge

The built-in `JudgeAgent` reads `criteria` each turn and decides whether
each is satisfied or violated. You don't write a `should_stop()` function.
`Judgment.should_terminate` plus the `max_turns` ceiling does it.

`must_happen` criteria that fail by the end of the run land in
`SimulationResult.rules_broken`. `must_not_happen` violations can terminate
the run immediately depending on the judge's verdict.

## Sanitization

When persona `background`, scenario `context`, or `goal` come from external
input (a CSV, a ticket export, anything user-supplied), wrap them through
`evaluatorq.common.sanitize.delimit()` before passing to the runner. The
shared sanitize util wraps content in `<data>` tags and escapes closing tags
to prevent prompt injection from leaking into the simulator's system prompt.

```python
from evaluatorq.common.sanitize import delimit

scenario = Scenario(
    name="from-ticket",
    goal=delimit(row["ticket_summary"]),
    context=delimit(row["ticket_body"]),
)
```
