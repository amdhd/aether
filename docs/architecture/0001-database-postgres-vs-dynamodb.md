# 0001 — PostgreSQL + pgvector over DynamoDB

**Status:** Accepted

## Context

Aether stores users, conversations, messages, tasks, notes, usage logs, and
refresh tokens, and it powers an **analytics dashboard** (tokens/day, tool-usage
breakdown) and **semantic note search** (vector similarity). Many AWS reference
chatbots use DynamoDB for "conversational memory," so the choice deserves a
record.

## Decision

Use **RDS PostgreSQL 16 with the `pgvector` extension** as the single system of
record.

## Rationale

1. **Vector search in the same store.** Notes are embedded and retrieved by
   cosine distance via `pgvector`. DynamoDB has no native vector search — it
   would require a second system (OpenSearch/Kendra) alongside the operational
   data.
2. **Relational queries and aggregations.** The analytics view is literally
   `GROUP BY day, SUM(tokens)`; tasks filter by status, notes by tag, messages
   order by id. These are trivial in SQL; in DynamoDB each needs a pre-planned
   index or a scan.
3. **Referential integrity and transactions.** Refresh-token rotation with
   reuse detection and `ON DELETE CASCADE` (users → conversations → messages)
   rely on foreign keys and transactions Postgres enforces natively.

## Consequences / trade-offs

- **Cost:** RDS bills hourly even when idle; DynamoDB is serverless (~$0 idle).
  Mitigated by the ephemeral `apply/destroy` model.
- **Ops:** a managed instance to size and patch vs a fully serverless table.

## When DynamoDB would win

If the data model were a simple append-and-fetch conversation log keyed by
`conversation_id` (as in the reference chatbots), DynamoDB's serverless
key-value model would be the better fit. Aether's data is relational + vector,
so Postgres is correct here.
