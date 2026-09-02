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

  statement {
    # The role actually missing s3:GetObjectAcl/ec2:DescribeSubnets --
    # confirmed via `aws iam simulate-principal-policy` directly (implicitDeny
    # on both), after diginyaya-eb-service-prod (the OTHER EB role, already
    # fixed) simulated as cleanly "allowed" on the same two actions. This is
    # the EC2 instance itself (via AWSElasticBeanstalkWebTier, which doesn't
    # cover these two), not the service role that orchestrates the
    # deployment -- a fourth distinct role in this whole chain, after the
    # GitHub Actions deploy role (#17/#19/#20/#21) and the EB service role
    # (#22).
    sid       = "EbInstanceHealthAgent"
    actions   = ["s3:GetObjectAcl"]
    resources = ["arn:aws:s3:::elasticbeanstalk-${var.aws_region}-${data.aws_caller_identity.current.account_id}/*"]
  }
}

data "aws_iam_policy_document" "eb_instance_ec2_describe" {
  # ec2:DescribeSubnets (like most EC2 Describe* actions) doesn't support
  # resource-level permissions -- AWS requires Resource: "*" for it.
  statement {
    sid       = "EbInstanceEc2Describe"
    actions   = ["ec2:DescribeSubnets"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "eb_instance_ec2_describe" {
  name   = "diginyaya-eb-instance-ec2-describe-${var.environment}"
  role   = aws_iam_role.eb_instance.id
  policy = data.aws_iam_policy_document.eb_instance_ec2_describe.json
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
    # EB scopes different operations to different ARN resource TYPES under
    # the same app -- application/, applicationversion/, environment/, and
    # configurationtemplate/ are each their own namespace, not sub-paths of
    # "application/diginyaya*" (confirmed the hard way: that single pattern
    # covers the application itself but not the applicationversion the
    # actual deploy creates -- AccessDenied on
    # elasticbeanstalk:CreateApplicationVersion on an applicationversion/...
    # ARN was the very next error after every S3/CreateStorageLocation issue
    # was fixed). Listing all four scoped to "diginyaya*" up front rather
    # than discovering each remaining one via another failed deploy.
    sid     = "ElasticBeanstalkDeploy"
    actions = ["elasticbeanstalk:*"]
    resources = [
      "arn:aws:elasticbeanstalk:${var.aws_region}:${data.aws_caller_identity.current.account_id}:application/diginyaya*",
      "arn:aws:elasticbeanstalk:${var.aws_region}:${data.aws_caller_identity.current.account_id}:applicationversion/diginyaya*",
      "arn:aws:elasticbeanstalk:${var.aws_region}:${data.aws_caller_identity.current.account_id}:environment/diginyaya*",
      "arn:aws:elasticbeanstalk:${var.aws_region}:${data.aws_caller_identity.current.account_id}:configurationtemplate/diginyaya*",
    ]
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
    # elasticbeanstalk:CreateStorageLocation above is the EB-service-level
    # permission, but under the hood it actually creates a real S3 bucket --
    # that needs its own S3-level grant too (confirmed the hard way, again:
    # got past the first error only to hit AccessDenied on s3:CreateBucket
    # next). This is AWS's own fixed, well-known bucket-naming convention for
    # the EB deployment bucket, not something this repo chose.
    # CreateBucket alone got past the previous error only to hit AccessDenied
    # on s3:PutBucketOwnershipControls next -- the EB SDK runs a fixed
    # sequence of bucket-provisioning calls internally (create -> ownership
    # controls -> encryption -> public-access-block -> policy) that has
    # nothing to do with this app, so granting the full documented set here
    # up front rather than discovering each one via another failed deploy.
    sid = "ElasticBeanstalkManagedBucket"
    actions = [
      "s3:CreateBucket", "s3:PutBucketPolicy", "s3:GetBucketPolicy",
      "s3:PutBucketOwnershipControls", "s3:PutEncryptionConfiguration",
      "s3:PutBucketPublicAccessBlock", "s3:PutBucketAcl",
      "s3:PutObject", "s3:GetObject", "s3:ListBucket", "s3:GetObjectAcl",
    ]
    resources = [
      "arn:aws:s3:::elasticbeanstalk-${var.aws_region}-${data.aws_caller_identity.current.account_id}",
      "arn:aws:s3:::elasticbeanstalk-${var.aws_region}-${data.aws_caller_identity.current.account_id}/*",
    ]
  }
  statement {
    # A FIFTH distinct role needing this exact pair (s3:GetObjectAcl,
    # ec2:DescribeSubnets), after diginyaya-eb-instance-prod (#24). This one
    # is the CALLING role itself -- the credentials the GitHub Actions
    # runner uses to invoke elasticbeanstalk:UpdateEnvironment in the first
    # place. Found via `aws elasticbeanstalk describe-events` showing the
    # exact same AccessDenied errors firing ONE SECOND after "Environment
    # update is starting" -- too fast to be real instance-level work, i.e.
    # a synchronous pre-flight check the API call itself performs using the
    # CALLER's credentials, not (only) a role EB assumes internally later.
    # Confirmed via `aws iam simulate-principal-policy` against this exact
    # role: implicitDeny on both, the same way #24 found for the instance
    # role. ec2:DescribeSubnets needs Resource: "*", same reasoning as
    # ElasticBeanstalkAccountLevel above.
    sid       = "ElasticBeanstalkDeployEc2Describe"
    actions   = ["ec2:DescribeSubnets"]
    resources = ["*"]
  }
  statement {
    # EB manages every environment update through an internal, auto-created
    # CloudFormation stack (named awseb-e-<id>-stack) -- polling deployment
    # progress needs read access to that stack, which is a CloudFormation
    # permission, not an Elastic Beanstalk one, so ElasticBeanstalkDeploy's
    # elasticbeanstalk:* grant above doesn't cover it (confirmed the hard
    # way: AccessDenied on cloudformation:GetTemplate on the exact
    # awseb-e-*-stack ARN was the next error after fixing
    # CreateApplicationVersion). Scoped to the awseb-* naming prefix EB
    # itself uses, not "*", but the stack's numeric suffix is unpredictable
    # ahead of creation so the prefix wildcard is as tight as this can get.
    sid = "ElasticBeanstalkManagedStack"
    actions = [
      "cloudformation:GetTemplate", "cloudformation:DescribeStacks",
      "cloudformation:DescribeStackEvents", "cloudformation:DescribeStackResource",
      "cloudformation:DescribeStackResources",
    ]
    resources = ["arn:aws:cloudformation:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stack/awseb-*/*"]
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
