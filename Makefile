# Aether — deploy convenience wrapper.
# Lifecycle: base-up (once) → image → up → [demo] → down.
# Requires: terraform, awscli, docker (buildx). AWS creds via your usual profile.

REGION      ?= ap-southeast-1
PREFIX      ?= aether
L1          := infra/terraform/layer1_persistent
L2          := infra/terraform/layer2_ephemeral
TF          := terraform
IMAGE_TAG   ?= latest

# Pull values out of layer1 state so the other targets don't hardcode them.
ECR_URL      = $(shell cd $(L1) && $(TF) output -raw ecr_repository_url 2>/dev/null)
WEB_BUCKET   = $(shell cd $(L1) && $(TF) output -raw web_bucket 2>/dev/null)
CF_ID        = $(shell cd $(L1) && $(TF) output -raw cloudfront_distribution_id 2>/dev/null)
CF_DOMAIN    = $(shell cd $(L1) && $(TF) output -raw cloudfront_domain_name 2>/dev/null)
ALB_URL      = $(shell cd $(L2) && $(TF) output -raw api_alb_url 2>/dev/null)

.PHONY: help fmt validate init base-up image web up migrate down verify-clean destroy-all

help:
	@echo "base-up       apply layer1 (persistent: ECR, S3, CloudFront, secrets)"
	@echo "image         build+push the ARM64 API image to ECR"
	@echo "web           build the SPA and sync to S3 + invalidate CloudFront"
	@echo "up            apply layer2 (VPC/ALB/ECS/RDS) then run migrations"
	@echo "migrate       run 'alembic upgrade head' as a one-off ECS task"
	@echo "down          destroy layer2 (stops all hourly billing)"
	@echo "verify-clean  assert no orphan Elastic IPs / ENIs remain"
	@echo "HA:  make up HA=true   (Multi-AZ RDS + autoscaling + Redis)"

TFVARS = $(if $(HA),-var 'high_availability=$(HA)',) -var 'image_tag=$(IMAGE_TAG)'

fmt:
	$(TF) -chdir=infra/terraform fmt -recursive

validate:
	cd $(L1) && $(TF) init -backend=false -input=false >/dev/null && $(TF) validate
	cd $(L2) && $(TF) init -backend=false -input=false >/dev/null && $(TF) validate

# --- Persistent base ---
base-up:
	cd $(L1) && $(TF) init -backend-config=backend.hcl && $(TF) apply

# --- Build & publish artifacts ---
image:
	@test -n "$(ECR_URL)" || { echo "run 'make base-up' first (no ECR url)"; exit 1; }
	aws ecr get-login-password --region $(REGION) \
	  | docker login --username AWS --password-stdin $(firstword $(subst /, ,$(ECR_URL)))
	docker buildx build --platform linux/arm64 -t $(ECR_URL):$(IMAGE_TAG) --push ./api

web:
	@test -n "$(WEB_BUCKET)" || { echo "run 'make base-up' first (no bucket)"; exit 1; }
	cd web && VITE_API_URL="$(ALB_URL)/api/v1" npm ci && npm run build
	aws s3 sync web/dist s3://$(WEB_BUCKET) --delete
	aws cloudfront create-invalidation --distribution-id $(CF_ID) --paths '/*'
	@echo "Frontend: https://$(CF_DOMAIN)"

# --- Ephemeral stack ---
up:
	cd $(L2) && $(TF) init -backend-config=backend.hcl && $(TF) apply $(TFVARS)
	$(MAKE) migrate

migrate:
	@echo "Running alembic upgrade head as a one-off Fargate task..."
	@cluster=$$(cd $(L2) && $(TF) output -raw ecs_cluster); \
	 taskdef=$$(cd $(L2) && $(TF) output -raw task_definition_arn); \
	 subnets=$$(cd $(L2) && $(TF) output -json app_subnet_ids | tr -d '[]"' ); \
	 sg=$$(cd $(L2) && $(TF) output -raw api_security_group_id); \
	 aws ecs run-task --cluster $$cluster --task-definition $$taskdef \
	   --launch-type FARGATE --region $(REGION) \
	   --network-configuration "awsvpcConfiguration={subnets=[$$subnets],securityGroups=[$$sg],assignPublicIp=DISABLED}" \
	   --overrides '{"containerOverrides":[{"name":"api","command":["alembic","upgrade","head"]}]}'

down:
	cd $(L2) && $(TF) destroy $(TFVARS)
	$(MAKE) verify-clean

# --- Teardown safety net (principle #5) ---
verify-clean:
	@echo "Checking for orphaned Elastic IPs..."
	@eips=$$(aws ec2 describe-addresses --region $(REGION) \
	   --query "Addresses[?AssociationId==null].PublicIp" --output text); \
	 test -z "$$eips" && echo "  none" || { echo "  ORPHAN EIPs: $$eips"; exit 1; }
	@echo "Checking for stranded network interfaces..."
	@enis=$$(aws ec2 describe-network-interfaces --region $(REGION) \
	   --filters Name=status,Values=available \
	   --query "NetworkInterfaces[].NetworkInterfaceId" --output text); \
	 test -z "$$enis" && echo "  none" || echo "  available ENIs (review): $$enis"

destroy-all: down
	cd $(L1) && $(TF) destroy
