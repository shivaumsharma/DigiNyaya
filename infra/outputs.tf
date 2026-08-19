output "backend_url" {
  description = "Elastic Beanstalk environment URL -- use for pre-cutover verification (smoke_http.py, load_test.py) before touching DNS."
  value       = aws_elastic_beanstalk_environment.backend.endpoint_url
}

output "frontend_url" {
  description = "CloudFront distribution URL -- use for pre-cutover verification before touching DNS."
  value       = "https://${aws_cloudfront_distribution.frontend.domain_name}"
}

output "rds_endpoint" {
  description = "RDS instance endpoint (host:port)."
  value       = aws_db_instance.diginyaya.endpoint
  sensitive   = true
}

output "documents_bucket" {
  value = aws_s3_bucket.documents.bucket
}

output "frontend_bucket" {
  value = aws_s3_bucket.frontend.bucket
}

output "ecr_repository_url" {
  value = aws_ecr_repository.backend.repository_url
}

output "cloudfront_distribution_id" {
  description = "Needed for the CI deploy job's cache-invalidation step."
  value       = aws_cloudfront_distribution.frontend.id
}

output "github_actions_role_arn" {
  description = "Add as the AWS_DEPLOY_ROLE_ARN GitHub Actions secret/variable."
  value       = aws_iam_role.github_actions_deploy.arn
}
