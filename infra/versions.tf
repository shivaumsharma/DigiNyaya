terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # One-time manual bootstrap required first -- see README.md. Uncomment and
  # fill in the bucket name once that bucket exists; Terraform can't create
  # its own state backend.
  # backend "s3" {
  #   bucket       = "diginyaya-terraform-state-<your-account-id>"
  #   key          = "diginyaya/terraform.tfstate"
  #   region       = "eu-north-1"
  #   encrypt      = true
  #   use_lockfile = true
  # }
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
