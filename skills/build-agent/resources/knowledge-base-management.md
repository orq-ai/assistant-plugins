# Knowledge Base Management

Complete guide for creating, populating, searching, and managing orq.ai Knowledge Bases. For API reference, see [api-reference.md](api-reference.md).

## Contents
- Check existing Knowledge Bases
- Create a Knowledge Base
- Upload files and create datasources
- Chunking strategies
- Add chunks with metadata
- Search with metadata filters
- Connect KB to prompts
- Update and delete
- Common pitfalls

## Check Existing Knowledge Bases

Before creating a new KB, check what exists:
- Use `search_entities` with `type: "knowledge"` to find existing KBs
- Review whether an existing KB can be reused or extended

## Create a Knowledge Base

Required parameters:

| Parameter | Description | Example |
|-----------|-------------|---------|
| `key` | Unique identifier (alphanumeric) | `support-docs` |
| `embedding_model` | Format: `supplier/model_name` | `cohere/embed-english-v3.0` |
| `path` | Project/folder location | `my-project/knowledge` |
| `type` | Always `"internal"` for managed KBs | `internal` |

Save the returned `id` — you need it for all subsequent operations.

Available embedding models (must be activated in AI Router before use):
- `cohere/embed-english-v3.0`
- `openai/text-embedding-3-small`
- `openai/text-embedding-3-large`

## Upload Files and Create Datasources

Supported formats: TXT, PDF, DOCX, CSV, XML — max 10MB per file.

A **datasource** is a logical container within a KB that holds chunks. You can:
- Link it to an uploaded file (auto-chunked by orq.ai)
- Create an empty datasource for manual chunk insertion

Upload a file first, save the returned `id`, then create a datasource referencing that file ID.

## Chunking Strategies

Chunking configuration is managed through the KB settings at [my.orq.ai](https://my.orq.ai/) or automatically when creating datasources with uploaded files.

| Strategy | Best for | Key parameters |
|----------|----------|----------------|
| **token** | Consistent sizing for LLM windows | `chunk_size` (512), `chunk_overlap` (0) |
| **sentence** | Prose, articles, narratives | `chunk_size` (512), `min_sentences_per_chunk` (1) |
| **recursive** | Structured documents (markdown, code) | `chunk_size` (512), `separators` (`["\n\n", "\n", " ", ""]`), `min_characters_per_chunk` (24) |
| **semantic** | Mixed content, requires embedding model | `embedding_model` (required), `threshold` (`"auto"`), `mode` (`window`/`sentence`), `similarity_window` (1) |
| **agentic** | Complex documents needing LLM judgment | `model` (required), `chunk_size` (1024), `candidate_size` (128), `min_characters_per_chunk` (24) |

## Add Chunks with Metadata

When adding chunks manually, include metadata for filtering:

```json
[
  {
    "text": "Chunk content here",
    "metadata": {
      "source": "support-docs",
      "topic": "billing",
      "client_id": "acme"
    }
  }
]
```

Metadata guidelines:
- Values must be **string, number, or boolean** — no nested objects
- Use metadata for filtering: `client_id`, `source`, `topic`, `filetype`
- Avoid large text blobs in metadata fields

## Search with Metadata Filters

Filter operators:

| Operator | Description | Example |
|----------|-------------|---------|
| `eq` | Equal to | `{"topic": {"eq": "billing"}}` |
| `ne` | Not equal | `{"topic": {"ne": "internal"}}` |
| `gt`, `gte` | Greater than (or equal) | `{"version": {"gte": 2}}` |
| `lt`, `lte` | Less than (or equal) | `{"version": {"lt": 5}}` |
| `in` | Value in array | `{"topic": {"in": ["billing", "support"]}}` |
| `nin` | Value not in array | `{"topic": {"nin": ["draft"]}}` |
| `and` | Logical AND | `{"and": [{"topic": {"eq": "billing"}}, {"client_id": {"eq": "acme"}}]}` |
| `or` | Logical OR | `{"or": [{"topic": {"eq": "billing"}}, {"topic": {"eq": "support"}}]}` |

## Connect KB to Prompts

Once populated, connect a Knowledge Base to a deployment:

1. In the prompt configuration, go to the **Knowledge Base** tab
2. Select the Knowledge Base by its key
3. Reference it in the prompt with `{{key}}` syntax (the key highlights in blue)
4. Configure the query type:
   - **Last User Message** — the user's message becomes the retrieval query
   - **Query** — a predefined or variable-driven query (use `{{query}}` for dynamic input)

## Update and Delete

- Update a KB key or configuration via PATCH
- Delete a KB or datasource via DELETE
- **Always confirm with user before deleting** — these are destructive operations

## Common Pitfalls

| Pitfall | Fix |
|---------|-----|
| **Embedding model not activated** | Enable the model in AI Router before creating a KB |
| **File too large** | Max 10MB per file; split large documents before uploading |
| **Metadata with nested objects** | Only flat key-value pairs (string/number/boolean) are supported |
| **Chunking without testing** | Always search after chunking to verify retrieval quality |
| **Wrong chunking strategy** | Token chunking splits mid-sentence; use sentence or recursive for readable chunks |
