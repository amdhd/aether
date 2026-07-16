# 0003 — External LLM (DeepSeek) over Amazon Bedrock

**Status:** Accepted (revisit per deployment)

## Context

The agent needs a tool-calling chat model with streaming and a "thinking" mode.
AWS-native designs typically use **Amazon Bedrock**. Aether currently calls
**DeepSeek** over HTTPS via the OpenAI-compatible SDK, with **OpenAI** for
embeddings.

## Decision

Keep the LLM provider **external and swappable** (DeepSeek for generation,
OpenAI for embeddings), reached from private subnets via a **NAT Gateway**.

## Rationale

1. **Provider independence.** The generation layer is isolated behind one client
   module, so the model can be swapped without touching the rest of the app —
   the same code runs locally and on any cloud.
2. **Cost / capability fit** for this project at build time, using an
   OpenAI-compatible API the app already speaks.
3. **No lock-in** to a single cloud's model catalog for a portfolio app that
   must also run outside AWS.

## Consequences / trade-offs

- **Egress + secrets:** outbound calls require NAT egress and API keys in
  Secrets Manager (both already in the design).
- **Data residency / governance:** traffic leaves AWS to a third party — a real
  consideration for regulated workloads.
- Misses Bedrock conveniences (IAM-native auth, private connectivity via VPC
  endpoints, Guardrails, Knowledge Bases).

## When Bedrock would win

For an AWS-committed, compliance-sensitive deployment, switch generation to
**Amazon Bedrock** (e.g. Claude) for IAM-native access, in-VPC connectivity, and
managed guardrails — and consider **Bedrock Knowledge Bases** or **Kendra** for
managed RAG in place of self-managed embeddings. The generation module is
designed to make that swap localized.
