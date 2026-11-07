# --- GitHub Actions deploy role (OIDC, no long-lived keys) ---
#
# The deploy workflow assumes this role to push the API image and roll the ECS
# service. Its trust policy is the real boundary on who can deploy, so it lives
# here, where it can be reviewed, rather than being created by hand.
#
# Trust is pinned to tokens minted for this repository's `production` GitHub
# environment. A pull request's jobs never run in that environment, so a PR —
# even one from a branch in this repo — cannot assume the role. Restrict the
# environment to the main branch (and add required reviewers) in the repo's
# Settings → Environments; that rule is what makes "production" mean "main".
#
# Opt-in: leave github_repository empty and nothing here is created.

locals {
  github_deploy_enabled = var.github_repository != ""
  github_oidc_provider_arn = local.github_deploy_enabled ? (
    var.create_github_oidc_provider
    ? aws_iam_openid_connect_provider.github[0].arn
    : data.aws_iam_openid_connect_provider.github[0].arn
  ) : null

  account_id     = data.aws_caller_identity.current.account_id
  ecs_cluster    = var.name_prefix
  ecs_service    = "${var.name_prefix}-api"
  ecs_family     = "${var.name_prefix}-api"
  ecs_migrate    = "${var.name_prefix}-migrate"
  ecs_arn_prefix = "arn:aws:ecs:${var.aws_region}:${local.account_id}"
}

# One provider per account. Set create_github_oidc_provider = false if the
# account already has one (another project created it) and reuse that instead.
resource "aws_iam_openid_connect_provider" "github" {
  count          = local.github_deploy_enabled && var.create_github_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = local.github_deploy_enabled && !var.create_github_oidc_provider ? 1 : 0
  url   = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "github_deploy_trust" {
  count = local.github_deploy_enabled ? 1 : 0

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # StringEquals, not StringLike: an exact subject leaves no wildcard for a
    # branch, tag or PR ref to match.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:environment:${var.github_deploy_environment}"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  count                = local.github_deploy_enabled ? 1 : 0
  name                 = "${var.name_prefix}-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.github_deploy_trust[0].json
  max_session_duration = 3600
  tags                 = { Name = "${var.name_prefix}-github-deploy" }
}

# Exactly what .github/workflows/deploy.yml calls, scoped to this stack's
# repository, cluster, service and task family wherever the API allows it.
data "aws_iam_policy_document" "github_deploy" {
  count = local.github_deploy_enabled ? 1 : 0

  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # account-level call; has no resource form
  }

  statement {
    sid = "EcrPushApiImage"
    actions = [
      "ecr:DescribeRepositories",
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:BatchGetImage",
    ]
    resources = [aws_ecr_repository.api.arn]
  }

  statement {
    sid = "TaskDefinitions"
    # Neither action supports resource-level permissions.
    actions   = ["ecs:DescribeTaskDefinition", "ecs:RegisterTaskDefinition"]
    resources = ["*"]
  }

  statement {
    sid       = "RunMigrationTask"
    actions   = ["ecs:RunTask"]
    resources = ["${local.ecs_arn_prefix}:task-definition/${local.ecs_migrate}:*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = ["${local.ecs_arn_prefix}:cluster/${local.ecs_cluster}"]
    }
  }

  statement {
    sid       = "WatchMigrationTask"
    actions   = ["ecs:DescribeTasks"]
    resources = ["${local.ecs_arn_prefix}:task/${local.ecs_cluster}/*"]
  }

  statement {
    sid       = "RollService"
    actions   = ["ecs:DescribeServices", "ecs:UpdateService"]
    resources = ["${local.ecs_arn_prefix}:service/${local.ecs_cluster}/${local.ecs_service}"]
  }

  # Registering and running the task definition hands ECS its two roles. Only
  # those (layer2's name_prefix roles), and only to ECS tasks.
  statement {
    sid     = "PassApiTaskRoles"
    actions = ["iam:PassRole"]
    resources = [
      "arn:aws:iam::${local.account_id}:role/${var.name_prefix}-exec-*",
      "arn:aws:iam::${local.account_id}:role/${var.name_prefix}-task-*",
    ]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  count  = local.github_deploy_enabled ? 1 : 0
  name   = "deploy-api"
  role   = aws_iam_role.github_deploy[0].id
  policy = data.aws_iam_policy_document.github_deploy[0].json
}
