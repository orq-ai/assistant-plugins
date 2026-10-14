# Tool Description Best Practices

Tool descriptions are the primary mechanism agents use to decide which tool to call. A vague description causes tool selection errors — the #1 source of agent failures.

---

## The 5 Rules of Tool Descriptions

### Rule 1: State WHEN to Use the Tool

Bad: `"Gets weather information"`
Good: `"Use this tool when the user asks about current weather conditions, forecasts, or temperature for a specific location. Do NOT use for historical weather data."`

### Rule 2: State What the Tool Does NOT Do

Agents confuse similar tools. Explicitly state boundaries.

Bad: `"Searches the database"`
Good: `"Searches the customer database by name or email. Does NOT search order history — use search_orders for that. Does NOT create or modify records."`

### Rule 3: Describe Required Parameters Precisely

Include format, constraints, and examples for every parameter.

Bad:
```json
{
  "name": "search_customers",
  "parameters": {
    "query": { "type": "string", "description": "Search query" }
  }
}
```

Good:
```json
{
  "name": "search_customers",
  "parameters": {
    "query": {
      "type": "string",
      "description": "Customer name (partial match) or full email address. Examples: 'John Smith', 'john@example.com'. Minimum 2 characters."
    }
  }
}
```

### Rule 4: Document the Return Value

Agents need to know what to expect back to interpret results correctly.

Bad: `"Returns customer data"`
Good: `"Returns a list of matching customers with fields: id, name, email, account_status ('active', 'suspended', 'closed'), and created_at (ISO 8601). Returns empty list if no matches found."`

### Rule 5: Include Error Cases

Tell the agent what errors look like so it can handle them.

Good: `"Returns { error: 'not_found' } if the customer ID doesn't exist. Returns { error: 'rate_limited' } if too many requests — wait 5 seconds and retry."`

---

## Template for Tool Descriptions

```
Use this tool to [PRIMARY ACTION] when [TRIGGER CONDITION].

Input: [PARAMETER DESCRIPTIONS with format, constraints, examples]

Returns: [RETURN VALUE DESCRIPTION with fields, types, edge cases]

Do NOT use this tool for: [EXPLICIT BOUNDARIES — what similar-sounding tasks should use a different tool]

Error handling: [COMMON ERRORS and what they mean]
```

---

## Common Mistakes

| Mistake | Problem | Fix |
|---------|---------|-----|
| One-word descriptions | Agent can't distinguish tools | Write 2-3 sentences minimum |
| No negative boundaries | Agent confuses similar tools | Add "Do NOT use for..." |
| Missing parameter formats | Agent sends wrong argument types | Specify type, format, constraints, examples |
| No return value description | Agent misinterprets results | Describe fields, types, and empty/error cases |
| Overlapping tool descriptions | Agent randomly picks between tools | Make each tool's trigger condition unique |

---

## Checklist Before Deploying an Agent

For each tool attached to your agent:

- [ ] Description states WHEN to use the tool (trigger condition)
- [ ] Description states what the tool does NOT do (boundaries)
- [ ] Every parameter has type, format, constraints, and example
- [ ] Return value is described with field names and types
- [ ] Error cases are documented
- [ ] No two tools have overlapping trigger conditions
- [ ] Description is written from the agent's perspective ("Use this tool when...")
