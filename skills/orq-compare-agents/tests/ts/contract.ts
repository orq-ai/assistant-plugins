// Every TypeScript claim resources/evaluatorq-api.md makes, as types the compiler
// must accept. Nothing here runs: `tsc --noEmit` is the whole assertion, and it is
// the right oracle because the risk is a rename — `invokeEvaluatorRequest` was
// already renamed once under the docs.
import type { DataPoint, DataPointResult, Evaluator, Job } from "@orq-ai/evaluatorq";
import { evaluatorq, job } from "@orq-ai/evaluatorq";
import { Orq } from "@orq-ai/node";

// The skill documents `evaluatorq()` as returning DataPointResult[], not void.
const results: Promise<DataPointResult[]> = evaluatorq("name", {
  data: [{ inputs: { q: "x" }, expectedOutput: "y" }] satisfies DataPoint[],
  jobs: [] as Job[],
  evaluators: [] as Evaluator[],
  // camelCase, and `parallelism` — TypeScript has no `datapointParallelism`.
  parallelism: 1,
  print: false,
  description: "d",
  path: "Team/Sprint",
});

// The platform-dataset input is `{ datasetId }`, camelCase, with optional messages.
const fromDataset = evaluatorq("name", {
  data: { datasetId: "ds_1", includeMessages: true },
  jobs: [] as Job[],
});

// `job()` takes the name first, then the handler.
const agentJob: Job = job("MyAgent", async (data: DataPoint) => ({
  name: "MyAgent",
  output: String(data.inputs.q),
}));

// The request key is `invokeEvaluatorRequest`, never `requestBody`, and the
// response is flat: `.value`, not `.value.value`.
async function invoke(orq: Orq) {
  const result = await orq.evals.invoke({
    id: "eval_1",
    invokeEvaluatorRequest: { query: "q", output: "o", reference: "r" },
  });
  const value: unknown = result.value;
  const explanation: string | null | undefined = result.explanation;
  return { value, explanation };
}

export { results, fromDataset, agentJob, invoke };
