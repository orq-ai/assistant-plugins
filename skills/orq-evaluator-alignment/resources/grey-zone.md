# Step 6: work out why the examples are hard, then ask about it

## 6. Work out *why* those examples are hard, then ask about it
Rather than labelling each example one at a time, group them by what makes them hard
and ask a few questions that each settle a whole group at once.

1. **Assemble** the payload and read it into context:
   ```
   uv run scripts/grey_zone.py assemble --run_dir <run_dir>
   ```
   `grey_zone_payload.json` gives each example's answer split, how wobbly it was, one
   of the judge's own explanations, and the (shortened) input it judged. *(Only one
   explanation per example is available — evaluatorq collapses the repetitions to
   one; a known v1 limit.)*

   **Don't `Read` `queue.json` or `stability.json` wholesale** — they carry every
   full input and every repetition, and reading them blows straight past the context
   budget. `grey_zone_payload.json` is the view you work from.

   **The one exception:** if answering a question genuinely needs a field the payload
   doesn't carry — a truncated passage, an exact figure, a row flagged
   `input_source: "fallback"` — look up **that one datapoint** by its `source_index`
   and read only its record. Say you're doing it and why. A single row is cheap; the
   whole file is not. Guessing because the rule said no is the worst of the three.

   **Then say what you actually got to see, before you analyse it.** The `budget`
   block tells you how many examples came through (`n_confusers`), how many the token
   budget dropped (`n_dropped_by_budget`), how many stable spot-check rows were held
   back (`n_low_flip_excluded`), and how many inputs were shortened (`n_truncated`,
   `total_chars_elided`; per example, `input_chars_shown` of `input_chars_original`).
   Say it in one plain line — *"all 3 came through in full, nothing shortened"*, or
   *"48 of 80; 12 had long inputs cut down, the worst showing 600 of 41,200
   characters"*.

   Two more that the elision numbers **cannot** tell you, so check them explicitly:
   - **`n_fallback_input` > 0** — those rows' inputs were reassembled from what the
     trace captured, because the judge template couldn't be inverted. They may be
     missing a field the judge had, and no character count will reveal it. Say so,
     and use the single-datapoint lookup below before drawing a conclusion from one.
   - **`n_no_rationale` > 0** — the judge gave no usable explanation for those,
     which happens most on an exact tie. That's the perverse case: the most evenly
     split example, where seeing both sides would help most, arrives with nothing to
     read. Don't quote the tie-break notice as if it were reasoning; say it's absent
     and reason from the input.
   - **`n_dropped_cross_model` > 0** — the queue lists second-model disagreers
     *after* the instability ranking, so the token budget drops those first. On a
     judge that rarely wavers they were the whole reason there was anything to look
     at. Raise `--max_tokens` and re-run rather than open-code without them.

   Each confuser also carries `reason`: `instability` (the judge disagreed with
   itself) or `cross_model` (it held steady and a second model disagreed). They are
   different kinds of hard — don't describe one as the other.

   This matters because a conclusion drawn from 1% of a transcript is confidently
   wrong in a way nothing downstream catches. Flag any group whose examples were
   heavily cut, and lean on the judge's own explanation for those. If they want to
   see more, raise `--max_chars` (more of each) or `--max_tokens` (more of them) and
   re-run — it's pure recomputation from `queue.json`, so it costs nothing.
2. **Group them by what makes them hard.** Not by topic — by the thing the current
   rubric doesn't settle (e.g. "sarcasm handled inconsistently", "abuse that's being
   quoted, not said", "no clear line for how severe counts as severe"). Step 4 found
   *which* examples it wobbled on; here you work out *why*.
3. **Ask one question per group — aim for 1–5 total.**  ⟵ GATE
   Ask about the rule, not the example, so one answer settles the whole group:
   - yes/no judge → which side of the line ("Should sarcasm aimed at a group count as abuse?")
   - category judge → where two labels divide ("When is something `spam` rather than `promotional`?")
   - score judge → the threshold ("Above what score would you call this severe?")

   Ask in chat, one at a time, and show an example or two so the question is
   concrete. What you want back is a short rule, not a verdict on each example.
4. **Write `grey_zone_policy.json`** from their answers. Per group record
   `{id, question, answer, rule, member_source_indices}`; then apply each rule to its
   examples to derive `{source_index, value, [tolerance], grey_zone_id, label_source}`
   — `true/false` for yes/no; one **already-declared** label for categories; for
   scores a target **plus a `tolerance` band** (asking for an exact number back is
   unrealistic). Carry the evaluator's `verdict_space` into the file; `apply` checks
   every value against it and refuses a label the judge cannot emit. **Never invent a
   new label or move the scale** — the rewritten judge has to answer in the same
   terms as the old one, or nothing is comparable.

   **Then read the derived labels back.**  ⟵ GATE
   You applied their rule; they didn't label these points. Those labels are what
   step 8 grades the new judge against, so show them the result in one short pass —
   *"applying that, I'd mark these three as pass and this one as fail; anything you'd
   flip?"* — and set `label_source: "human_confirmed"` on the ones they confirm,
   `"derived"` on the rest (the default). It's one message, and without it the whole
   validation is one model checking its own reading of the rule.
5. **Turn the answers into guidance** for the rewrite:
   ```
   uv run scripts/grey_zone.py apply --run_dir <run_dir>
   ```
   Checks the policy and writes `aggregated.md`. Go to step 7.
6. **Look at the steady ones too, briefly.** You promised this at step 5 and it is
   the only check on the blind spot the whole method has. One extra pass, held
   separately from the grey zones:
   ```
   uv run scripts/grey_zone.py assemble --run_dir <run_dir> --include_low_flip
   ```
   Read the `low_flip_sample: true` rows and ask the user whether the judge got them
   right — no grouping, no rules, just *"it was completely sure about these; do you
   agree?"* If any is wrong, say so plainly: the judge is confidently wrong
   somewhere, and nothing in the instability ranking will ever find that. **Re-run
   `assemble` without the flag afterwards** so `grey_zone_payload.json` goes back to
   being the grey-zone view. Don't fold these into the policy — they aren't grey
   zones, and step 8 re-judges them separately with `--with_low_flip`.

**If the examples don't group cleanly**, or the user would rather just look at them
one by one, open the scoring UI instead of doing the chat Q&A:
```
uv run scripts/serve_annotation.py --run_dir <run_dir>
```
They get the right control for the judge's type — Pass/Fail, one button per label, or
a number on the scale — plus an optional one-line "why". It saves as they go and can
be resumed. Then:
```
uv run scripts/recommend.py --run_dir <run_dir>
uv run scripts/aggregate.py --run_dir <run_dir>
```
Both routes end at `aggregated.md`. When both `grey_zone_policy.json` and
`annotations.json` exist, steps 7 and 8 score against the **union** of the two, keyed
by `source_index` — so switching to the UI part-way through the grey-zone route keeps
both halves of the labelling. Where a datapoint appears in both, the annotation wins,
since the UI is where you go to correct a policy label.

---

Next: step 7 — read [rewrite-and-create.md](rewrite-and-create.md).
