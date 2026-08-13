terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Bootstrapped manually (see README.md) -- account 753779157603's state
  # bucket, versioning enabled.
  backend "s3" {
    bucket       = "diginyaya-terraform-state-753779157603"
    key          = "diginyaya/terraform.tfstate"
    region       = "eu-north-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "diginyaya"
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
