# --- Secrets, via SSM Parameter Store (SecureString) rather than plaintext
# EB environment variables ---

resource "aws_ssm_parameter" "sarvam_api_key" {
  name  = "/diginyaya/${var.environment}/SARVAM_API_KEY"
  type  = "SecureString"
  value = var.sarvam_api_key
}

resource "aws_ssm_parameter" "jwt_secret" {
  name  = "/diginyaya/${var.environment}/DIGINYAYA_JWT_SECRET"
  type  = "SecureString"
  value = var.jwt_secret
}

resource "aws_ssm_parameter" "database_url" {
  name  = "/diginyaya/${var.environment}/DIGINYAYA_DB"
  type  = "SecureString"
  value = "postgresql+psycopg://${var.db_username}:${var.db_password}@${aws_db_instance.diginyaya.endpoint}/${var.db_name}"
}

# --- EB EC2 instance profile: what the running backend container can do ---

data "aws_iam_policy_document" "eb_instance_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "eb_instance" {
  name               = "diginyaya-eb-instance-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.eb_instance_assume_role.json
}

resource "aws_iam_role_policy_attachment" "eb_instance_ecr" {
  role       = aws_iam_role.eb_instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_role_policy_attachment" "eb_instance_web_tier" {
  role       = aws_iam_role.eb_instance.name
  policy_arn = "arn:aws:iam::aws:policy/AWSElasticBeanstalkWebTier"
}

data "aws_iam_policy_document" "eb_instance_scoped" {
  statement {
    sid       = "DocumentsBucketReadWrite"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.documents.arn}/*"]
  }

  statement {
    sid       = "SsmParameterRead"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = [
      aws_ssm_parameter.sarvam_api_key.arn,
      aws_ssm_parameter.jwt_secret.arn,
      aws_ssm_parameter.database_url.arn,
    ]
  }
}

resource "aws_iam_role_policy" "eb_instance_scoped" {
  name   = "diginyaya-eb-instance-scoped-${var.environment}"
  role   = aws_iam_role.eb_instance.id
  policy = data.aws_iam_policy_document.eb_instance_scoped.json
}

resource "aws_iam_instance_profile" "eb_instance" {
  name = "diginyaya-eb-instance-${var.environment}"
  role = aws_iam_role.eb_instance.name
}

# --- GitHub Actions OIDC role: what CI can do (ECR push + EB/S3/CloudFront
# deploy), scoped to this one repo and these specific resources -- no
# long-lived access keys. Assumes an OIDC provider for token.actions.
# githubusercontent.com already exists on this account (create once,
# account-wide, not per-project, if it doesn't: see AWS's GitHub OIDC docs). ---

data "aws_iam_policy_document" "github_actions_assume_role" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = ["repo:shivaumsharma/DigiNyaya:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_actions_deploy" {
  name               = "diginyaya-github-actions-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_actions_assume_role.json
}

data "aws_iam_policy_document" "github_actions_deploy" {
  statement {
    sid       = "EcrPush"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "EcrPushToOneRepo"
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage", "ecr:PutImage", "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart", "ecr:CompleteLayerUpload",
    ]
    resources = [aws_ecr_repository.backend.arn]
  }
  statement {
    sid       = "ElasticBeanstalkDeploy"
    actions   = ["elasticbeanstalk:*"]
    resources = ["arn:aws:elasticbeanstalk:${var.aws_region}:${data.aws_caller_identity.current.account_id}:application/diginyaya*"]
  }
  statement {
    # CreateStorageLocation provisions/verifies the EB-managed S3 bucket
    # (elasticbeanstalk-<region>-<account>) that einaregilsson/beanstalk-deploy
    # uploads app versions to before creating an application version -- an
    # account/region-level action, not an application-scoped one, so AWS
    # requires Resource: "*" for it specifically (confirmed the hard way:
    # scoping it under ElasticBeanstalkDeploy's application/diginyaya* ARN
    # above failed with AccessDenied on the real first deploy).
    sid       = "ElasticBeanstalkAccountLevel"
    actions   = ["elasticbeanstalk:CreateStorageLocation"]
    resources = ["*"]
  }
  statement {
    sid       = "FrontendBucketSync"
    actions   = ["s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.frontend.arn, "${aws_s3_bucket.frontend.arn}/*"]
  }
  statement {
    sid       = "CloudFrontInvalidate"
    actions   = ["cloudfront:CreateInvalidation"]
    resources = [aws_cloudfront_distribution.frontend.arn]
  }
}

resource "aws_iam_role_policy" "github_actions_deploy" {
  name   = "diginyaya-github-actions-deploy"
  role   = aws_iam_role.github_actions_deploy.id
  policy = data.aws_iam_policy_document.github_actions_deploy.json
}
