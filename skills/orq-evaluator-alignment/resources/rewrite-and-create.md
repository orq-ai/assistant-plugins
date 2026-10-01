# Step 7: rewrite the judge, create it only on approval

## 7. Show the proposed rewrite, then create it only if they say yes  ⟵ GATE

**First, name the model that will do the rewriting.** Before running anything here,
tell the user which model writes the new prompt and offer the alternatives — one
short exchange, not a lecture:

> *"I'll use **deepseek-v4-pro** through your orq workspace to write the new prompt —
> that's the default, and it bills to orq like everything else in this run. It needs
> to be enabled on your workspace; I'll check before spending anything. If you'd
> rather use a different model, tell me which, or I can use a Claude subagent
> instead if you have the CLI set up."*

Three ways to go, all set in `config.toml`:
- **`backend = "orq_router"`** (default) with `backend_model = ""` → `deepseek/deepseek-v4-pro`.
- **a different model** → set `backend_model` to its **`refId`** from `GET /v2/models`,
  e.g. `groq/openai/gpt-oss-120b`. It must be the provider-qualified id, not the
  short display alias — the alias can route to the wrong provider or 404.
- **`backend = "claude_subagent"`** → shells out to `claude -p`, spending Claude
  credits instead of workspace credits. Needs the CLI installed and logged in.

Both scripts below preflight the model with one tiny call and stop with a plain
reason if it isn't reachable — a model can be listed in the registry and still
refuse for want of a provider key. If that happens, relay the reason and re-offer
the three options rather than retrying.

```
uv run scripts/rewrite_eval.py --run_dir <run_dir>
uv run scripts/create_eval.py --run_dir <run_dir>          # shows the diff, creates nothing
```
The rewrite is guarded: it can't change which fields the judge reads, drop a label, or
move the scale. The second command **shows** what would change — the guidance it drew
on, the before/after prompt diff, and whether those guards held — and `create_eval.py
--approve` actually enforces all three (variable set, verdict space, preservation),
refusing to create unless every one passed. Walk the user through it: what changed,
and which of their answers drove it.

**Only after they say yes:**
```
uv run scripts/create_eval.py --run_dir <run_dir> --approve
```
(`--edits <file>` folds in their own wording.) This creates a **new** judge alongside
the old one — same answer type, named after the original with `-aligned-<timestamp>`,
placed in the same orq project as the source — and records which evaluator it came
from. **The original is never touched.** If they say no, stop; nothing is created.

---

Next: step 8 (optional) — read [retest.md](retest.md). Otherwise go to the final summary in [SKILL.md](../SKILL.md#final-summary).
