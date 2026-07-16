# Architecture Decision Records (ADRs)

Short records of the significant, defensible architecture decisions behind
Aether's AWS deployment. Each states the context, the decision, the trade-offs,
and when the alternative would win.

- [0001 — PostgreSQL + pgvector over DynamoDB](0001-database-postgres-vs-dynamodb.md)
- [0002 — ECS Fargate over serverless (Lambda/App Runner)](0002-compute-ecs-fargate.md)
- [0003 — External LLM (DeepSeek) over Amazon Bedrock](0003-llm-external-provider.md)

See also the [AWS architecture overview](../../README.md#cloud-architecture-aws)
and the [Terraform deploy runbook](../../infra/terraform/README.md).
