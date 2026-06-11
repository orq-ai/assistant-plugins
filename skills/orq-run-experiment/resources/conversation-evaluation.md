# Conversation Evaluation Methodology

Specialized methodology for evaluating **multi-turn conversational systems** (chatbots, support agents, coaching assistants). Use alongside the core 5-phase workflow in SKILL.md.

## Contents
- Conversation core principles
- Session-level vs turn-level evaluation hierarchy
- Multi-turn test data collection
- Perturbation scenarios
- Turn-level diagnostics
- Memory and coherence evaluation
- Conversation metrics summary

## Conversation Core Principles

### Start at Session Level

Always evaluate session-level success first: "Did the conversation achieve the user's goal?" Only drill into turn-level analysis when session-level failures need debugging.

### Isolate Multi-Turn vs Single-Turn Failures

Before building multi-turn test infrastructure, try reproducing failures as single-turn tests. If the single-turn version still fails, the root cause is NOT multi-turn (it is a retrieval, knowledge, or reasoning issue).

### Position-Dependent Degradation Is Common

Systems often degrade after 2-3 turns -- forgetting context, contradicting earlier responses, or drifting from instructions. Explicitly test for this.

## Session-Level vs Turn-Level Evaluation Hierarchy

| Level | What It Measures | When to Use | Cost |
|-------|-----------------|-------------|------|
| **Session level** | Did the full conversation achieve the goal? | Always -- start here | Low |
| **Turn level** | Was each individual response good? | Only when debugging specific failures | High |
| **Coherence** | Does the system maintain consistency across turns? | When contradiction/drift is suspected | Medium |
| **Memory** | Does the system remember earlier context? | When forgetting is suspected | Medium |
| **Efficiency** | How many turns to resolution? | When optimizing conversation flow | Low |

## Multi-Turn Test Data Collection

Three approaches for generating test conversations:

**Approach A -- Team simulation (recommended for starting):**
- Have 3-5 team members each simulate 10-15 realistic conversations
- Provide scenario cards describing the user's goal and persona
- Quickly generates 50-100+ diverse multi-turn traces
- Use a lightweight chat interface or orq.ai directly

**Approach B -- User simulation with LLM:**
- Use a second LLM to play the user role
- Define the user persona, goal, and behavior constraints
- The simulator sends messages; the agent under test responds
- Good for scaling but can produce unrealistic interactions
- Always have a human review a sample of simulated conversations

**Approach C -- N-1 testing (for targeted evaluation):**
- Take real conversations from production (or simulated ones)
- Provide the first N-1 turns as context
- Let the agent generate turn N
- Compare against the actual turn N (or expected behavior)
- More grounded but requires existing conversation data

## Perturbation Scenarios

| Perturbation | What It Tests | Example |
|-------------|--------------|---------|
| Goal change mid-conversation | Adaptability | "Actually, I changed my mind -- I want X instead" |
| Correction | Error handling | "No, I said the blue one, not the red one" |
| Ambiguity injection | Clarification behavior | "Can you help with the thing we discussed?" |
| Distraction | Focus maintenance | Unrelated question in the middle of a task |
| Escalation request | Handoff behavior | "I want to speak to a manager" |
| Contradiction | Consistency | Provide conflicting information across turns |
| Long gap | Memory retention | Resume conversation after many turns of other topics |

## Turn-Level Diagnostics

For sessions that failed, find the first failing turn:
- Read the conversation turn by turn
- Identify: at which turn did the conversation go off track?
- Was it a specific response that derailed things?
- Apply the "first upstream failure" principle

**Try to reproduce as a single-turn test:**
- Take the failing turn's context + input
- Run it as a standalone prompt
- If it still fails -> NOT a multi-turn issue (fix the prompt/model)
- If it passes as single-turn but fails in multi-turn -> genuine multi-turn issue (context management, memory, or position-dependent degradation)

**For genuine multi-turn failures, evaluate:**

| Check | Method | Evaluator Type |
|-------|--------|---------------|
| **Context retention** | Does the response reference earlier information correctly? | LLM-as-Judge with `{{log.messages}}` |
| **Instruction compliance** | Does the response follow rules stated earlier? | LLM-as-Judge checking specific rules |
| **Consistency** | Does the response contradict earlier statements? | LLM-as-Judge comparing turns |
| **Persona maintenance** | Does the character hold across turns? | LLM-as-Judge per-turn |
| **Position degradation** | Does quality drop after N turns? | Score by turn position, look for trends |

## Memory and Coherence Evaluation

**Within-session memory tests:**
- State a preference early in the conversation (e.g., "I prefer email communication")
- Check if the system honors it in later turns
- Vary the distance between stating and checking (1 turn, 5 turns, 10 turns)

**Cross-session memory tests (if using orq.ai Memory Stores):**
- Establish facts in Session 1
- Start a new Session 2
- Check if the system remembers facts from Session 1
- Test with: user preferences, conversation history, agreed-upon actions

**Coherence across long conversations:**
- Test with conversations of increasing length: 5, 10, 20, 50 turns
- Look for: contradictions, forgotten context, repeated questions, persona drift
- Plot quality scores against turn position to detect degradation curves

## Conversation Metrics Summary

| Metric | Level | What It Tells You |
|--------|-------|-------------------|
| **Session success rate** | Session | % of conversations achieving the goal |
| **Turns to resolution** | Session | Efficiency of the conversation |
| **First-failure turn** | Turn | Where conversations go wrong |
| **Context retention score** | Turn | Memory quality across turns |
| **Consistency score** | Coherence | Contradiction rate across turns |
| **Position degradation curve** | Coherence | Quality trend as conversations lengthen |
| **Cross-session recall** | Memory | Retention of facts across sessions |
