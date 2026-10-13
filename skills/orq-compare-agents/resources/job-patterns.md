# Job Patterns for Agent Comparison

Framework-specific job patterns for use with evaluatorq. Each job wraps an agent invocation so evaluatorq can run it against a dataset.

Pick the pattern matching each agent's framework, then assemble them into the comparison script.

---

## orq.ai Agent

### Python

```python
@job("OrqAgent")
async def orq_agent_job(data: DataPoint, row: int):
    with Orq(api_key=ORQ_API_KEY, server_url=ORQ_BASE_URL) as orq:
        response = orq.responses.create(
            model="agent/<AGENT_KEY>",
            input=data.inputs["query"],
        )

    # output is a list of message items; collect the assistant text blocks
    text = ""
    for msg in response.output or []:
        for block in msg.get("content", []):
            if block.get("type") == "output_text":
                text += block.get("text", "")

    return {
        "agent": "OrqAgent",
        "query": data.inputs["query"],
        "response": text,
    }
```

> **Import:** Use `from orq_ai_sdk import Orq` (package: `pip install orq-ai-sdk`). The module name is `orq_ai_sdk`, NOT `orq`.

### TypeScript

```typescript
const orqAgentJob = job("OrqAgent", async (data) => {
  const orq = new Orq({ apiKey: process.env.ORQ_API_KEY });
  // Mirrors POST /v3/router/responses (the authority); the Node SDK is generated from the same spec.
  const response = await orq.responses.create({
    model: "agent/<AGENT_KEY>",
    input: data.inputs.query,
  });

  let text = "";
  for (const msg of response.output ?? []) {
    for (const block of msg.content ?? []) {
      if (block.type === "output_text") text += block.text;
    }
  }
  return { agent: "OrqAgent", query: data.inputs.query, response: text };
});
```

> **Important:** Invoke the agent with `responses.create(model="agent/<AGENT_KEY>")`. If you fall back to the deprecated `agents.*` surface, use `agents.responses.create()`, NOT `agents.invoke()` — `invoke()` returns an async A2A task (status: "submitted"), not the actual response. See [gotchas](gotchas.md).

---

## LangGraph

### Python

```python
@job("LangGraph")
async def langgraph_job(data: DataPoint, row: int):
    from agent import agent

    result = await asyncio.to_thread(
        agent.invoke,
        {"messages": [("user", data.inputs["query"])]},
    )
    return {
        "agent": "LangGraph",
        "query": data.inputs["query"],
        "response": result["messages"][-1].content,
    }
```

### TypeScript

```typescript
import { wrapLangGraphAgent } from "@orq-ai/evaluatorq/langchain";

const langGraphJob = wrapLangGraphAgent("LangGraph", agent);
```

> `wrapLangGraphAgent` expects a LangChain-compatible agent instance.

---

## CrewAI

### Python

```python
@job("CrewAI")
async def crewai_job(data: DataPoint, row: int):
    from crew import crew

    result = await asyncio.to_thread(
        crew.kickoff,
        inputs={"query": data.inputs["query"]},
    )
    return {
        "agent": "CrewAI",
        "query": data.inputs["query"],
        "response": result.raw,
    }
```

### TypeScript

CrewAI is Python-native. Wrap it via HTTP if you need TypeScript:

```typescript
const crewJob = job("CrewAI", async (data) => {
  const res = await fetch("<CREW_ENDPOINT>", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query: data.inputs.query }),
  });
  const result = await res.json();
  return { agent: "CrewAI", query: data.inputs.query, response: result.output };
});
```

---

## OpenAI Agents SDK

### Python

```python
@job("OpenAIAgent")
async def openai_agent_job(data: DataPoint, row: int):
    from agents import Runner
    from my_agent import agent

    result = await Runner.run(agent, data.inputs["query"])
    return {
        "agent": "OpenAIAgent",
        "query": data.inputs["query"],
        "response": result.final_output,
    }
```

### TypeScript

```typescript
const openaiAgentJob = job("OpenAIAgent", async (data) => {
  const { Runner } = await import("@openai/agents");
  const { agent } = await import("./my-agent");

  const result = await Runner.run(agent, data.inputs.query);
  return {
    agent: "OpenAIAgent",
    query: data.inputs.query,
    response: result.finalOutput,
  };
});
```

---

## Vercel AI SDK

### Python (via HTTP)

```python
@job("VercelAgent")
async def vercel_agent_job(data: DataPoint, row: int):
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            "<VERCEL_AGENT_ENDPOINT>",
            json={"prompt": data.inputs["query"]},
        )
        result = r.json()

    return {
        "agent": "VercelAgent",
        "query": data.inputs["query"],
        "response": result["text"],
    }
```

### TypeScript

```typescript
import { wrapAISdkAgent } from "@orq-ai/evaluatorq/ai-sdk";

const vercelJob = wrapAISdkAgent("VercelAgent", agent);
```

> `wrapAISdkAgent` works with any Vercel AI SDK agent instance.

---

## Generic Agent (any callable)

### Python

```python
@job("MyAgent")
async def generic_agent_job(data: DataPoint, row: int):
    from my_agent import run_agent

    response = await asyncio.to_thread(run_agent, data.inputs["query"])
    return {
        "agent": "MyAgent",
        "query": data.inputs["query"],
        "response": response,
    }
```

### TypeScript

```typescript
const genericJob = job("MyAgent", async (data) => {
  const { runAgent } = await import("./my-agent");
  const response = await runAgent(data.inputs.query);
  return { agent: "MyAgent", query: data.inputs.query, response };
});
```

---

## orq.ai vs orq.ai (multiple agent keys)

When comparing two orq.ai agents, duplicate the orq.ai pattern with different keys:

### Python

```python
@job("OrqAgent-GPT4o")
async def orq_agent_a(data: DataPoint, row: int):
    # ... model="agent/agent-gpt4o" ...

@job("OrqAgent-Claude")
async def orq_agent_b(data: DataPoint, row: int):
    # ... model="agent/agent-claude" ...
```

### TypeScript

```typescript
const orqGpt4o = job("OrqAgent-GPT4o", async (data) => {
  // ... model: "agent/agent-gpt4o" ...
});

const orqClaude = job("OrqAgent-Claude", async (data) => {
  // ... model: "agent/agent-claude" ...
});
```
