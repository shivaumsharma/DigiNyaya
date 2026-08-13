data "aws_iam_policy_document" "eb_service_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["elasticbeanstalk.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "eb_service" {
  name               = "diginyaya-eb-service-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.eb_service_assume_role.json
}

resource "aws_iam_role_policy_attachment" "eb_service" {
  role       = aws_iam_role.eb_service.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSElasticBeanstalkEnhancedHealth"
}

resource "aws_elastic_beanstalk_application" "diginyaya" {
  name        = "diginyaya"
  description = "DigiNyaya backend (FastAPI, single-container Docker)"
}

resource "aws_elastic_beanstalk_environment" "backend" {
  name                = "diginyaya-backend-${var.environment}"
  application         = aws_elastic_beanstalk_application.diginyaya.name
  solution_stack_name = "64bit Amazon Linux 2023 v4.13.6 running Docker" # confirmed current via `aws elasticbeanstalk list-available-solution-stacks --region eu-north-1`

  setting {
    namespace = "aws:autoscaling:launchconfiguration"
    name      = "IamInstanceProfile"
    value     = aws_iam_instance_profile.eb_instance.name
  }

  setting {
    namespace = "aws:autoscaling:launchconfiguration"
    name      = "InstanceType"
    value     = var.eb_instance_type
  }

  setting {
    namespace = "aws:autoscaling:launchconfiguration"
    name      = "SecurityGroups"
    value     = aws_security_group.backend.id
  }

  setting {
    namespace = "aws:elasticbeanstalk:environment"
    name      = "EnvironmentType"
    value     = "SingleInstance" # no load balancer -- the "exactly one instance" scope boundary, by construction
  }

  setting {
    namespace = "aws:elasticbeanstalk:environment"
    name      = "ServiceRole"
    value     = aws_iam_role.eb_service.name
  }

  setting {
    namespace = "aws:ec2:vpc"
    name      = "VPCId"
    value     = data.aws_vpc.default.id
  }

  setting {
    namespace = "aws:ec2:vpc"
    name      = "Subnets"
    value     = join(",", data.aws_subnets.default.ids)
  }

  # SSM Parameter Store (iam.tf's aws_ssm_parameter resources) is the source
  # of truth for these three secrets -- never committed to the repo, never
  # baked into the Docker image. Terraform reads them back here so EB can
  # inject them as real container env vars at deploy time; the scoped
  # ssm:GetParameter grant on the instance role (iam.tf) exists so a future
  # move to resolving these at container-startup instead doesn't need an
  # IAM change.
  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "SARVAM_API_KEY"
    value     = aws_ssm_parameter.sarvam_api_key.value
  }

  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "DIGINYAYA_JWT_SECRET"
    value     = aws_ssm_parameter.jwt_secret.value
  }

  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "DIGINYAYA_DB"
    value     = aws_ssm_parameter.database_url.value
  }

  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "DIGINYAYA_STORAGE_PROVIDER"
    value     = "s3"
  }

  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "DIGINYAYA_S3_BUCKET"
    value     = aws_s3_bucket.documents.bucket
  }

  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "DIGINYAYA_FRONTEND_URL"
    # Deliberately NOT a reference to aws_cloudfront_distribution.frontend --
    # that would make this environment depend on CloudFront existing before
    # it can be created, which defeats the point of standing the backend up
    # independently while CloudFront waits on AWS account verification.
    # Update this to the real CloudFront URL (a cheap, non-disruptive
    # `terraform apply`) once that distribution exists.
    value = var.frontend_url_override
  }

  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "DIGINYAYA_ENV"
    value     = "production"
  }
}
