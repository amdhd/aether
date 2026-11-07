# Aether — deploy convenience wrapper.
# Lifecycle: base-up (once) → image → up → [demo] → down.
# Requires: terraform, awscli, docker (buildx). AWS creds via your usual profile.

REGION      ?= ap-southeast-1
PREFIX      ?= aether
L1          := infra/terraform/layer1_persistent
L2          := infra/terraform/layer2_ephemeral
TF          := terraform
# Images are tagged by commit (ECR tags are immutable). A dirty tree gets a
# -dirty suffix so an uncommitted build can never take a clean commit's tag;
# being immutable, a second dirty push of the same commit is refused — commit,
# or set IMAGE_TAG explicitly.
IMAGE_TAG   ?= $(shell git rev-parse --short=12 HEAD)$(shell git diff --quiet HEAD -- api || echo -dirty)

# Pull values out of layer1 state so the other targets don't hardcode them.
ECR_URL      = $(shell cd $(L1) && $(TF) output -raw ecr_repository_url 2>/dev/null)
WEB_BUCKET   = $(shell cd $(L1) && $(TF) output -raw web_bucket 2>/dev/null)
CF_ID        = $(shell cd $(L1) && $(TF) output -raw cloudfront_distribution_id 2>/dev/null)
CF_DOMAIN    = $(shell cd $(L1) && $(TF) output -raw cloudfront_domain_name 2>/dev/null)
FRONTEND_URL = $(shell cd $(L1) && $(TF) output -raw frontend_url 2>/dev/null)
ALB_URL      = $(shell cd $(L2) && $(TF) output -raw api_alb_url 2>/dev/null)

.PHONY: help fmt validate init base-up image web up migrate roll down verify-clean destroy-all eval lock

help:
	@echo "eval          run the RAG eval harness (RAGAS-equivalent metrics)"
	@echo "lock          re-pin api/requirements*.txt (with hashes) from the .in files"
	@echo "base-up       apply layer1 (persistent: ECR, S3, CloudFront, secrets)"
	@echo "image         build+push the ARM64 API image to ECR"
	@echo "web           build the SPA and sync to S3 + invalidate CloudFront"
	@echo "up            apply layer2 (VPC/ALB/ECS/RDS) then run migrations"
	@echo "migrate       run 'alembic upgrade head' as a one-off ECS task and wait for it"
	@echo "roll          move the service onto Terraform's task definition and wait"
	@echo "down          destroy layer2 (stops all hourly billing)"
	@echo "verify-clean  assert no orphan Elastic IPs / ENIs remain"
	@echo "HA:  make up HA=true   (Multi-AZ RDS + autoscaling + Redis)"

TFVARS = $(if $(HA),-var 'high_availability=$(HA)',) -var 'image_tag=$(IMAGE_TAG)'

# --- RAG evaluation ---
# Runs the RAGAS-equivalent eval suite (faithfulness / context precision /
# answer relevancy) over the golden dataset. Uses the LLM backend when
# DEEPSEEK_API_KEY is set, else the keyless offline heuristic backend. Point at
# Postgres + pgvector via EVAL_DATABASE_URL for true semantic retrieval.
eval:
	cd api && python -m app.eval.run

# --- Python dependency lock ---
# requirements*.in list what we depend on; requirements*.txt are generated from
# them with every transitive package pinned and hashed, so pip installs exactly
# what was locked or refuses. Resolved for Linux/macOS on Python 3.12 (the image
# and CI) regardless of the machine this runs on. Needs uv.
LOCK = uv pip compile --universal --python-version 3.12 --generate-hashes --quiet --custom-compile-command "make lock"

lock:
	cd api && $(LOCK) requirements.in -o requirements.txt
	cd api && $(LOCK) requirements-dev.in -c requirements.txt -o requirements-dev.txt

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
	@api=$$(cd $(L2) && $(TF) output -raw api_alb_url); \
	 test -n "$$api" || { echo "ERROR: could not read api_alb_url — is layer2 applied and AWS_PROFILE set?"; exit 1; }; \
	 case "$$api" in http://*) \
	   echo "WARNING: API is HTTP-only ($$api). The HTTPS SPA can't call it"; \
	   echo "         (mixed content) and Secure cookies won't set, so login will fail."; \
	   echo "         Set api_domain_name/hosted_zone_name in layer2 for a working demo."; ;; \
	 esac; \
	 echo "Building SPA with VITE_API_URL=$$api"; \
	 (cd web && npm ci && VITE_API_URL="$$api" npm run build); \
	 grep -rq "$$api" web/dist/assets/ || { \
	   echo "ERROR: the built SPA does not contain $$api — VITE_API_URL never reached"; \
	   echo "       'vite build', so the bundle fell back to its localhost default."; \
	   exit 1; }
	aws s3 sync web/dist s3://$(WEB_BUCKET) --delete
	aws cloudfront create-invalidation --distribution-id $(CF_ID) --paths '/*'
	@echo "Frontend: $(FRONTEND_URL)"

# --- Ephemeral stack ---
# Order matters: the service's tasks connect as the app DB role, which the
# migration creates, and their health check requires a working database. So
# migrate (and wait for it), then roll the service onto the task definition
# Terraform just registered — the service ignores task-definition changes
# (CI registers its own revisions), so without this step an apply that changes
# secrets, env or the image would not reach the running tasks.
up:
	@aws ecr describe-images --repository-name $(PREFIX)-api --region $(REGION) \
	   --image-ids imageTag=$(IMAGE_TAG) >/dev/null 2>&1 || { \
	   echo "ERROR: no image tagged $(IMAGE_TAG) in ECR — run 'make image' first"; \
	   echo "       (or pass IMAGE_TAG=<an existing commit tag>)."; exit 1; }
	cd $(L2) && $(TF) init -backend-config=backend.hcl && $(TF) apply $(TFVARS)
	$(MAKE) migrate
	$(MAKE) roll

migrate:
	@echo "Running alembic upgrade head as a one-off Fargate task..."
	@cluster=$$(cd $(L2) && $(TF) output -raw ecs_cluster); \
	 taskdef=$$(cd $(L2) && $(TF) output -raw migrate_task_definition_arn); \
	 subnets=$$(cd $(L2) && $(TF) output -json app_subnet_ids | tr -d '[]"' ); \
	 sg=$$(cd $(L2) && $(TF) output -raw api_security_group_id); \
	 task=$$(aws ecs run-task --cluster $$cluster --task-definition $$taskdef \
	   --launch-type FARGATE --region $(REGION) \
	   --network-configuration "awsvpcConfiguration={subnets=[$$subnets],securityGroups=[$$sg],assignPublicIp=DISABLED}" \
	   --query 'tasks[0].taskArn' --output text); \
	 test -n "$$task" -a "$$task" != None || { echo "ERROR: migration task did not start"; exit 1; }; \
	 echo "Waiting for $$task ..."; \
	 aws ecs wait tasks-stopped --cluster $$cluster --tasks $$task --region $(REGION); \
	 code=$$(aws ecs describe-tasks --cluster $$cluster --tasks $$task --region $(REGION) \
	   --query 'tasks[0].containers[0].exitCode' --output text); \
	 echo "migration exit code: $$code"; \
	 test "$$code" = "0" || { echo "ERROR: migration failed — see the 'migrate' log stream"; exit 1; }

# Point the service at Terraform's current task definition and wait until the
# new tasks pass their (database-readiness) health check.
roll:
	@cluster=$$(cd $(L2) && $(TF) output -raw ecs_cluster); \
	 service=$$(cd $(L2) && $(TF) output -raw ecs_service); \
	 taskdef=$$(cd $(L2) && $(TF) output -raw task_definition_arn); \
	 echo "Rolling $$service onto $$taskdef ..."; \
	 aws ecs update-service --cluster $$cluster --service $$service \
	   --task-definition $$taskdef --region $(REGION) >/dev/null && \
	 aws ecs wait services-stable --cluster $$cluster --services $$service --region $(REGION) && \
	 echo "Service stable."

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
