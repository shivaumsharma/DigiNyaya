# DigiNyaya AWS infrastructure (Terraform)

Provisions everything the AWS migration plan calls for: RDS Postgres, two S3
buckets (evidence documents + frontend static site), CloudFront, ECR, an
Elastic Beanstalk Single-Instance environment for the backend, and the IAM
roles/security groups tying it together. All in `eu-north-1` (Stockholm).

**Nothing here has been applied.** This is reviewable code — read the plan
(`terraform plan`) before ever running `terraform apply`, and treat every
`apply` as a real, billable action.

## One-time bootstrap (do this yourself, not from an agent session)

Terraform needs somewhere to store its state, and that somewhere can't be
Terraform-managed itself (chicken-and-egg) — create it once, manually:

```bash
# 1. Configure credentials locally (never paste these into a chat session).
aws configure
# Enter your IAM access key / secret / region (eu-north-1) when prompted.

# 2. Create the Terraform state bucket (name must be globally unique --
#    substitute your own account id or a name of your choosing).
aws s3 mb s3://diginyaya-terraform-state-<your-account-id> --region eu-north-1
aws s3api put-bucket-versioning \
  --bucket diginyaya-terraform-state-<your-account-id> \
  --versioning-configuration Status=Enabled
```

Then fill in that bucket name in `versions.tf`'s `backend "s3"` block
(currently commented out with a placeholder), and:

```bash
cd infra
terraform init
terraform plan    # review every resource before applying anything
terraform apply   # only after reviewing the plan output
```

## What this does NOT do

- Does not touch DNS/Route53 or a custom domain — deferred until domain
  ownership (`diginyaya.in`) is confirmed. Everything here is reachable via
  its AWS-assigned URL (`*.elasticbeanstalk.com`, `*.cloudfront.net`) until
  a deliberate cutover step.
- Does not delete or touch anything on Render — the two platforms run in
  parallel until AWS is verified end-to-end (see the migration plan's
  sequencing section).
- Does not seed the database — `alembic upgrade head` (already wired into
  `backend/Dockerfile`'s CMD) creates the schema on first deploy.

## Cost

Sized to fit inside a $139.99 / 164-day AWS credit budget: RDS
`db.t4g.micro` Single-AZ + EB `t3.small` Single-Instance (no load balancer)
≈ $29–32/month. S3 + CloudFront + ECR are near-zero at this traffic level.
Check free-tier eligibility on the account before applying — it may reduce
this further for the first 12 months.
