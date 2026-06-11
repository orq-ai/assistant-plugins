# Dataset Curation Guide (Mode 4)

Clean, deduplicate, balance, and augment an existing dataset — improving experiment reliability through higher quality data.

## Contents
- Phase 1: Fetch and inventory
- Phase 2: Quality analysis
- Phase 3: Quality report template
- Phase 4: Execute improvements
- Phase 5: Fill coverage gaps
- Phase 6: Validate improvements

## Phase 1: Fetch and Inventory

1. **Find the target dataset:**
   - Use `search_entities` with `type: "dataset"` to find datasets
   - Use HTTP API to get full dataset details and datapoints

2. **Build an inventory:**
   ```
   | Metric | Value |
   |--------|-------|
   | Total datapoints | [N] |
   | Unique inputs | [N] |
   | Duplicate inputs | [N] |
   | With expected output | [N] |
   | Without expected output | [N] |
   | Tags/categories | [list] |
   | Date range | [earliest] - [latest] |
   ```

## Phase 2: Quality Analysis

3. **Duplicate detection:**
   - Exact duplicates: identical input text
   - Near-duplicates: inputs that differ only in whitespace, capitalization, or trivial phrasing
   - Report duplicate groups with counts

4. **Class balance analysis:**
   - Group datapoints by category/tag/difficulty
   - Compute distribution:
     ```
     | Category | Count | Percentage | Assessment |
     |----------|-------|-----------|------------|
     | Easy queries | 150 | 60% | Over-represented |
     | Medium queries | 70 | 28% | Balanced |
     | Hard queries | 20 | 8% | Under-represented |
     | Edge cases | 10 | 4% | Under-represented |
     ```
   - Flag any category below 15% or above 40% as imbalanced

5. **Coverage gap identification:**
   - Map datapoints against expected input dimensions (topic, difficulty, length, edge cases)
   - Identify dimensions with zero or low coverage
   - Compare against real production traffic patterns (if available)

6. **Contradictory datapoint detection:**
   - Find datapoints with similar inputs but different expected outputs
   - These indicate labeling errors or ambiguous criteria

7. **Quality scoring per datapoint:**
   - Is the input realistic? (not synthetic-looking noise)
   - Is the expected output correct and complete?
   - Does the metadata/tags accurately describe the datapoint?

## Phase 3: Quality Report

ALWAYS use this exact template:

```markdown
# Dataset Quality Report
**Dataset:** [name]
**Total datapoints:** [N]
**Date:** [date]

## Issues Found

### Duplicates: [N] duplicate groups ([M] datapoints)
[List top 5 duplicate groups with example inputs]

### Balance Issues
[Distribution table with flagged categories]

### Coverage Gaps
[List dimensions with missing coverage]

### Contradictions: [N] pairs
[List contradictory datapoint pairs]

### Low-Quality Datapoints: [N]
[List with reasons]

## Recommended Actions
1. Remove [N] exact duplicates
2. Remove [N] low-quality datapoints
3. Add [N] datapoints for [underrepresented category]
4. Resolve [N] contradictions
```

## Phase 4: Execute Improvements

> **Destructive actions below — always confirm with the user via `AskUserQuestion` before executing.**

9. **Deduplicate** (with user confirmation):
   - Keep the most complete version of each duplicate group
   - Use `delete_datapoints` to remove duplicates
   - Report: "[N] duplicates removed, [M] datapoints remaining"

10. **Remove low-quality datapoints** (with user confirmation):
    - Show each datapoint marked for removal with reason
    - Use `delete_datapoints` after confirmation

11. **Resolve contradictions** (with user input):
    - Present each contradiction pair to the user
    - Ask which expected output is correct
    - Use `update_datapoint` to fix or `delete_datapoints` to remove

12. **Rebalance** (with user confirmation):
    - For over-represented categories: optionally remove excess datapoints
    - For under-represented categories: flag for augmentation in Phase 5

## Phase 5: Fill Coverage Gaps

13. **Generate targeted new datapoints** for gaps:
    - Use Mode 1 (structured generation) for maximum diversity when filling gaps
    - Focus on: under-represented categories, missing edge cases, missing difficulty levels
    - Use `create_datapoints` to add new datapoints

14. **Validate new datapoints:**
    - Review generated datapoints for quality
    - Ensure they don't duplicate existing ones
    - Verify expected outputs are correct

## Phase 6: Validate Improvements

15. **Re-run quality analysis:**
    - Repeat Phase 2 on the updated dataset
    - Confirm: duplicates removed, balance improved, coverage gaps filled

16. **Document changes.** ALWAYS use this template:
    ```markdown
    ## Dataset Curation Changelog
    **Date:** [date]
    **Before:** [N] datapoints
    **After:** [M] datapoints

    ### Changes
    - Removed [X] exact duplicates
    - Removed [Y] low-quality datapoints
    - Resolved [Z] contradictions
    - Added [W] new datapoints for [categories]
    ```
