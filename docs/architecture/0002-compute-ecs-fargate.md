# 0002 — ECS Fargate over serverless (Lambda / App Runner)

**Status:** Accepted

## Context

The API is a FastAPI app that **streams chat over Server-Sent Events (SSE)**,
runs an in-process tool-calling agent loop, and holds pooled async connections
to Postgres. It is already containerized. It needs to sit in a VPC with private
egress to a managed database.

## Decision

Run the API as a container on **AWS ECS Fargate** (Graviton/ARM64) behind an
**Application Load Balancer** inside a VPC.

## Rationale

1. **Long-lived streaming.** SSE responses are long-lived; an ALB + always-warm
   task handles them cleanly, without Lambda's response-streaming/timeout
   constraints or cold starts mid-stream.
2. **VPC-native, connection-pooled.** Fargate tasks live in private subnets with
   security-group-scoped access to RDS and a warm SQLAlchemy pool — a better fit
   than Lambda's per-invocation model (which pushes you toward RDS Proxy).
3. **Portability.** The same image runs locally (`docker compose`), on any
   container host, and on Fargate — no framework rewrite to a serverless handler.
4. **Graviton.** ARM64 Fargate is cheaper than x86 for equivalent performance.

## Consequences / trade-offs

- Always-on task cost vs Lambda's scale-to-zero. Mitigated by ephemeral
  `apply/destroy` and single-task demo sizing.
- More infrastructure to define (VPC, ALB, NAT) than a fully serverless stack —
  offset by reusable Terraform modules.

## When serverless would win

A bursty, request/response (non-streaming) API with spiky traffic and a NoSQL
backend would favor **Lambda + API Gateway** (scale-to-zero). **App Runner**
would be a lighter middle ground, but gives up VPC/ALB/WAF control that this
design uses deliberately.
