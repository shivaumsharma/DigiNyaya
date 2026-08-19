variable "aws_region" {
  description = "AWS region for every resource in this migration."
  type        = string
  default     = "eu-north-1"
}

variable "environment" {
  description = "Deployment environment name, used in resource naming."
  type        = string
  default     = "prod"
}

variable "db_instance_class" {
  description = "RDS instance class. db.t4g.micro fits the credit budget -- check free-tier eligibility on the account before applying."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_name" {
  description = "Postgres database name."
  type        = string
  default     = "diginyaya"
}

variable "db_username" {
  description = "Master username for the RDS instance."
  type        = string
  default     = "diginyaya_app"
}

variable "db_password" {
  description = "Master password for the RDS instance. Pass via TF_VAR_db_password or a *.auto.tfvars file that is git-ignored -- never commit this."
  type        = string
  sensitive   = true
}

variable "eb_instance_type" {
  description = "EC2 instance type for the Elastic Beanstalk Single-Instance environment."
  type        = string
  default     = "t3.small"
}

variable "sarvam_api_key" {
  description = "Sarvam API key, stored in SSM Parameter Store (SecureString) rather than a plaintext EB environment variable."
  type        = string
  sensitive   = true
}

variable "jwt_secret" {
  description = "DIGINYAYA_JWT_SECRET -- stored in SSM Parameter Store (SecureString). A stable value here is required in production, or every backend restart invalidates every live session."
  type        = string
  sensitive   = true
}

variable "frontend_url_override" {
  description = "DIGINYAYA_FRONTEND_URL for the backend's CORS config. Empty until the CloudFront distribution exists (see elastic_beanstalk.tf) -- set to https://<cloudfront-domain> once it's created, via terraform.tfvars or -var."
  type        = string
  default     = ""
}
