# Aether infrastructure (AWS, Terraform)

Two-layer Terraform for running Aether on AWS as an **ephemeral demo** you can
`make up` and `make down` — see the top-level [deployment plan](../../) for the
full architecture and cost breakdown.

## Layout

```
modules/            reusable building blocks (no provider/backend of their own)
  vpc/              subnets (public/app/db), IGW, NAT, routes, S3 gateway endpoint
  security_groups/  ALB → api → rds/redis ingress chain
  ecs/              cluster, task def (valueFrom secrets), ALB, service, autoscaling, 2 IAM roles
  rds/              Postgres 16 (+pgvector), HA/backup/deletion gated
layer1_persistent/  ECR, S3(OAC)+CloudFront, Secrets Manager, Budget alert   (leave up, ~$0.50/mo)
layer2_ephemeral/   VPC, ALB, ECS, RDS, (Redis), WAF, alarms                 (apply→demo→destroy)
```

The stacks keep **separate state**; `layer2` reads `layer1`'s outputs via a
`terraform_remote_state` data source, so destroying layer2 never touches the base.

## One-time setup

1. Create an S3 bucket for Terraform state (once, out-of-band).
2. In each stack: `cp backend.hcl.example backend.hcl` and fill in the bucket.
3. In `layer2_ephemeral`: `cp terraform.tfvars.example terraform.tfvars` and set
   `layer1_state_bucket`.

## Lifecycle (from the repo root)

```sh
make base-up          # apply layer1 once (persistent)
# put real values in the aether/app secret (SECRET_KEY, ENCRYPTION_KEY, DEEPSEEK_API_KEY, …)
make image            # build+push the ARM64 API image to ECR
make up               # apply layer2 + run DB migrations
make web              # build the SPA against the live ALB URL, sync to S3, invalidate
# ... demo ...
make down             # destroy layer2 → hourly billing stops; runs verify-clean
```

`make up HA=true` provisions the production-grade shape (Multi-AZ RDS,
autoscaling, ElastiCache Redis) from the same code — the single
`high_availability` toggle.

## Design decisions worth knowing

- **Frontend/API split.** CloudFront (layer1, persistent) serves the SPA from a
  private S3 bucket via OAC. The API is reached through the ALB (layer2)
  directly. Because the ALB DNS changes each `make up`, `make web` rebuilds the
  (tiny) SPA with the current `VITE_API_URL` and re-syncs — no app change, no
  stale origin. Frontend↔API is cross-origin, so refresh cookies use
  `Secure + SameSite=None` (already supported by the app).
  *Upgrade path:* a custom domain + Route53 alias gives the ALB a stable name,
  which lets CloudFront route `/api/*` to it for a single-origin (`SameSite=Lax`)
  setup.
- **Secrets** are injected into the task via `valueFrom` ARNs (never baked into
  the image or fetched in an entrypoint). Real values live in Secrets Manager,
  set out-of-band; Terraform only manages the container + an ignored placeholder.
- **Graviton (ARM64)** Fargate tasks — cheaper than x86 for equivalent perf.
  Build the image with `--platform linux/arm64` (the Makefile does).
- **Teardown safety** (RDS `skip_final_snapshot`/no deletion protection in demo,
  S3 `force_destroy`, `make verify-clean`) so `destroy` never hangs or leaks a
  billable orphan.

## Offline checks

```sh
make fmt
make validate         # init -backend=false + validate, both stacks
```
