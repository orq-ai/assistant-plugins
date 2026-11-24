---
name: orq-provider-credentials
description: >
  Use when someone wants to connect their own model provider credentials to
  an orq.ai workspace: an OpenAI, Anthropic or Google key, an Azure OpenAI or
  Azure Foundry deployment, an AWS Bedrock inference profile, a Vertex AI
  service account, or any OpenAI-compatible endpoint, and to check, rotate or
  remove them. Triggers on "connect my Azure OpenAI deployment to orq", "add
  my Anthropic key", "use my own Bedrock models", "rotate the key on my
  custom model". Do NOT use to pick the cheapest or best model for a workload,
  to call a model (use orq-invoke-deployment), or for orq API keys and
  workspace members (use orq-admin).
allowed-tools: Read, Write, AskUserQuestion, Skill, Bash(orq auth profile add:*), Bash(orq models list:*), Bash(orq models validate:*), Bash(orq models validate-aws-bedrock:*), Bash(orq models azure-foundry-deployments:*), Bash(orq models create:*), Bash(orq models create-openai-like:*), Bash(orq models create-vertex:*), Bash(orq models create-aws-bedrock:*), Bash(orq models delete:*), Bash(orq responses create:*)
metadata:
  verified: "2026-11-24"
  surface: cli
---

# Provider credentials

**Keys for the main providers are set in the Studio, not the CLI.** The integrations API behind the Studio's provider pages is private, so for OpenAI, Anthropic, Google and the rest of the Integrations list this skill gives the provider page link and checks the result afterwards. Custom models with their own endpoint and credentials (Azure, Bedrock, Vertex, OpenAI-compatible) are created with `orq models`.

## Gotchas (read first)

Probed 2026-11-24 with CLI 12.13.0, prod and staging. Access rules from orquesta-web `apps/api-tests/specs/models_test.go` at `d0c5139bae`.

- **Custom model writes need a management key.** `models create*`, `update*`, `delete`, `validate` and `validate-aws-bedrock` return 403 to every product API key (workspace, project and read-only). A project key got 403 live on prod and staging. Whether a Studio login session works instead was not checked.
- **`models validate --provider azure` with no other field is a safe preflight.** A product key gets 403 (seen live); a management key gets 400 for the missing `api_key` (API tests). Nothing is sent to Azure and no secret is involved.
- **`validate` covers Azure OpenAI only.** Bedrock has `validate-aws-bedrock`. `create-openai-like` and `create-vertex` probe the endpoint themselves before saving.
- **`models list` does not prove a key works.** It lists system models (`owner: system`) whether or not the workspace has its own key for that provider; custom models carry the workspace id in `owner`. A successful `responses create` shows the model is reachable, not which credential served it.
- **A stored API key cannot be changed in place.** `update-openai-like` re-probes with the stored encrypted key and has no `api_key` field; `models update` has no credential field at all. Rotating an OpenAI-compatible, Vertex or Azure credential means creating a replacement model and deleting the old one, and the replacement has a new `refId`.
- **Bedrock stores no secret on the model.** It reads credentials from an integration (`--auth-mode integration` with `--integration-id`) or from the deployment's own AWS identity (`pod-identity`). The integration is set up, and its credentials rotated, in the Studio; the model does not change.
- **Delete returns 200 without deleting** a system model or one that experiments still use; the response message says so. Read it before reporting a delete.

## When to use

- Connect, check, rotate or remove a model provider credential in an orq workspace
- Register an Azure, Bedrock, Vertex or OpenAI-compatible model with its own endpoint
- A call fails because the workspace has no key for that provider

## When NOT to use

- Choosing a model by price or quality: not this skill
- Calling a model or deployment: `orq-invoke-deployment`
- orq API keys, management keys, members: `orq-admin`
- The CLI's own login: `orq-cli`

## Constraints

- **NEVER** ask for, accept or repeat a secret in chat. If the user pastes one, tell them to rotate it at the provider.
- **NEVER** Read, `cat` or print a body file after the user has put a secret in it, and never print a response's `configuration` block.
- **ALWAYS** pass secrets through a file the user fills in, with `--from-file <path>`, never on the command line (see [`orq-cli`](../orq-cli/SKILL.md), profile section). Tell them to `chmod 600` the file and to delete it afterwards.
- **ALWAYS** confirm with AskUserQuestion before each create, and twice before a delete; then run the delete with `--force`.
- **ALWAYS** follow the credential rules in [`orq-cli`](../orq-cli/SKILL.md) (never expand `$ORQ_API_KEY`; `ORQ_API_KEY` wins over a login) and [`coding-agents.md`](../orq-cli/resources/coding-agents.md).

## Workflow Checklist

```
- [ ] 1. Which provider, which path (Studio page or custom model)
- [ ] 2. Main provider: link, user sets the key, one test call
- [ ] 3. Custom model: management key preflight
- [ ] 4. Custom model: validate, then create (each confirmed)
- [ ] 5. Verify and clean up
```

## Steps

1. **Pick the path.** OpenAI, Anthropic, Google AI and the other providers on the Studio's Integrations page: step 2. An Azure OpenAI or Foundry deployment, a Bedrock inference profile, a Vertex service account or an OpenAI-compatible endpoint: steps 3 to 5. Unsure: ask.
2. **Main provider.** Build the provider page link with [`orq-link`](../orq-link/SKILL.md): `router/providers/(provider:<name>)`, where `<name>` is the `provider` field of `orq models list -o json` (for example `openai`, `anthropic`, `google-ai`). Tell the user to paste the key on that page, never into chat. Then pick a model of that provider by its `refId` ([`model-slugs.md`](../orq-shared/resources/model-slugs.md); `model_id` is shared across providers):

   ```sh
   orq models list -o json -j "[?provider=='anthropic'].refId"
   ```

   After asking, one small call on it. A `completed` status shows the provider is reachable, not which credential served the call:

   ```sh
   orq responses create --model "<refId>" --input "Reply with OK" --max-output-tokens 16 --store=false -o json -j status
   ```

3. **Management key preflight** (custom models only):

   ```sh
   orq models validate --provider azure -o json
   ```

   403: the credential is a product API key. Ask the user for a management key (created on the [`orq-link`](../orq-link/SKILL.md) `admin/api-keys/management-keys` page, or with `orq-admin`) and to add it as a profile (`orq auth profile add <name> --api-key-file -`, see [`orq-cli`](../orq-cli/SKILL.md)) and rerun with `--profile <name>`. 400 about a missing `api_key`: go on.
4. **Validate, then create.** Read `orq models <command> --help` and `--example` for the body; do not guess fields. Write the body to a file with every non-secret field filled and the secret as `"<paste here>"`, tell the user to fill it in and `chmod 600` it, then pass only the path.
   - **Azure:** `orq models validate --from-file <path>` first (or `--integration-id` for workload identity, no key). `orq models azure-foundry-deployments --from-file <path>` (or `--integration-id`) lists a Foundry resource's deployments; never pass `--api-key`. Then confirm and `orq models create --from-file <path>`.
   - **Bedrock:** `orq models validate-aws-bedrock` with the inference profile ARN, region and auth mode first. Then confirm and `orq models create-aws-bedrock`.
   - **OpenAI-compatible:** confirm, then `orq models create-openai-like --from-file <path>`; it probes the endpoint before saving.
   - **Vertex:** confirm, then `orq models create-vertex --from-file <path>`; it probes Vertex before saving.

   Show the result with `-j '{id: id, refId: refId, provider: provider}'`, never the whole response.
5. **Verify and clean up.** The new model shows in `orq models list` with the workspace id as `owner`. After asking, one small `responses create` as in step 2. Remind the user to delete the body file.

**Rotate.** Create the replacement first (step 4), tell the user that its `refId` differs from the old one, and wait until every deployment and agent uses the new `refId`. Then delete the old model as below. Bedrock: rotate the integration's credentials in the Studio ([`orq-link`](../orq-link/SKILL.md) `admin/integrations`); the model stays.

**Remove.** Delete: confirm twice, `orq models delete <id> --force`, then read the response message; a 200 can mean "not deleted" (system model, or used by experiments).

## Done When

- Main provider: the user has the provider page link, and the test call completed or the user declined it
- Custom model: validated where a validate command exists, created after a yes, listed with the workspace as `owner`
- Every create was confirmed once and every delete twice; delete responses were read
- No secret in chat, argv or printed output; the user was told to delete the body file

## Open in orq.ai

- **Provider page:** `router/providers/(provider:<name>)` from [`studio-routes.md`](../orq-shared/resources/studio-routes.md) (unverified)
- **Integrations:** `admin/integrations` from the same file (unverified)

## Companion skills

- `orq-link`: builds the provider page and integrations links
- `orq-cli`: profiles, credential precedence, `--from-file`
- `orq-admin`: management keys, which custom model writes need
- `orq-invoke-deployment`: calling the model once it is connected

## Documentation & Resolution

**Lookup order: [`doc-resolution.md`](../orq-shared/resources/doc-resolution.md).** Live reads first (`orq models <command> --help`, `--example`), then [`studio-routes.md`](../orq-shared/resources/studio-routes.md).
