# Job Patterns for Agent Comparison

Framework-specific job patterns for use with evaluatorq. Each job wraps an agent invocation so evaluatorq can run it against a dataset.

Pick the pattern matching each agent's framework, then assemble them into the comparison script.

---

## orq.ai Agent

### Python — orq SDK (preferred)

Prefer the orq SDK for new code and when it is already imported. If orq is not imported and OpenAI already is, reuse OpenAI instead of adding a second SDK.

```python
from orq_ai_sdk import Orq

@job("OrqAgent")
async def orq_agent_job(data: DataPoint, row: int):
    # orq takes the host; its SDK adds /v3/router/responses.
    async with Orq(api_key=ORQ_API_KEY, server_url=ORQ_BASE_URL.rstrip("/")) as orq:
        response = await orq.responses.create_async(
            model="agent/<AGENT_KEY>", input=data.inputs["query"], stream=False,
        )
    if response.status != "completed":
        raise RuntimeError(f"Agent response {response.id}: {response.status}")
    text = "".join(
        part["text"]
        for item in response.output or [] if item.get("type") == "message"
        for part in item.get("content", []) if part.get("type") == "output_text"
    )
    return {"agent": "OrqAgent", "query": data.inputs["query"], "response": text}
```

### Python — existing OpenAI SDK

```python
from openai import AsyncOpenAI

@job("OrqAgent")
async def orq_agent_job(data: DataPoint, row: int):
    # ORQ_BASE_URL is the host, e.g. https://my.orq.ai (keep staging hosts verbatim).
    async with AsyncOpenAI(api_key=ORQ_API_KEY, base_url=f"{ORQ_BASE_URL.rstrip('/')}/v3/router") as client:
        response = await client.responses.create(
            model="agent/<AGENT_KEY>",
            input=data.inputs["query"],
        )
    if response.status != "completed":
        raise RuntimeError(f"Agent response {response.id}: {response.status}")
    return {"agent": "OrqAgent", "query": data.inputs["query"], "response": response.output_text}
```

> Install `orq-ai-sdk` for the preferred pattern. Use the OpenAI alternative when it is already imported and orq is not; do not install both just for inference. Both call general Responses. Keep the orq SDK for platform evaluator calls when needed.

### TypeScript — orq SDK (preferred)

```typescript
import { Orq } from "@orq-ai/node";

const orqAgentJob = job("OrqAgent", async (data) => {
  const query = data.inputs.query;
  if (typeof query !== "string") throw new Error("Expected a string inputs.query");
  const host = (process.env.ORQ_BASE_URL ?? "https://my.orq.ai").replace(/\/$/, "");
  const orq = new Orq({ apiKey: process.env.ORQ_API_KEY, serverURL: host });
  const response = await orq.responses.create({
    model: "agent/<AGENT_KEY>", input: query, stream: false,
  });
  if (!("output" in response) || response.status !== "completed") {
    throw new Error("Expected a completed non-streaming response");
  }
  const text = (response.output ?? [])
    .filter(item => item.type === "message")
    .flatMap(item => item.content ?? [])
    .filter(part => part.type === "output_text")
    .map(part => part.text).join("");
  return { agent: "OrqAgent", query, response: text };
});
```

### TypeScript — existing OpenAI SDK

```typescript
import OpenAI from "openai";

const orqAgentJob = job("OrqAgent", async (data) => {
  const query = data.inputs.query;
  if (typeof query !== "string") throw new Error("Expected a string inputs.query");
  const host = (process.env.ORQ_BASE_URL ?? "https://my.orq.ai").replace(/\/$/, "");
  const client = new OpenAI({ apiKey: process.env.ORQ_API_KEY, baseURL: `${host}/v3/router` });
  const response = await client.responses.create({
    model: "agent/<AGENT_KEY>",
    input: query,
  });
  if (response.status !== "completed") throw new Error(`Agent response ${response.id}: ${response.status}`);
  return { agent: "OrqAgent", query, response: response.output_text };
});
```

> **Important:** Use the general `POST /v3/router/responses` route, or `orq responses create --model "agent/<key>"` from a shell. Legacy agent invocation APIs are deprecated and require an explicit user request; never use them as an EU-model workaround. See [gotchas](gotchas.md).

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
