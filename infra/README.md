# AWS deployment

Everything runs on ECS Fargate behind one Application Load Balancer:

| Component | AWS service | Notes |
|-----------|-------------|-------|
| Scoring API + consumer | ECS Fargate service `fraudstream-api` (2 tasks) | each task runs the FastAPI app with `FS_RUN_CONSUMER=true`, so they share the Redis consumer group `scorer` |
| Dashboard | ECS Fargate service `fraudstream-dashboard` | nginx serves the React build and proxies `/api/` to `http://api:8000/` via ECS Service Connect |
| Queue | ElastiCache Redis 7 (`cache.t4g.micro`) | Redis Streams `transactions` / `alerts` |
| Alerts + feedback | RDS Postgres 16 (`db.t4g.micro`) | DSN stored in Secrets Manager, injected as `FS_DATABASE_URL` |
| Model artifacts | S3 bucket (versioned) | the deploy workflow bakes `s3://<bucket>/models/` into the API image |
| Images | ECR `fraudstream/api`, `fraudstream/dashboard` | lifecycle policy keeps the last 20 |
| Deploys | GitHub Actions via OIDC role | no static AWS keys |
| Replay demo producer | optional ECS service (`enable_replay = true`) | streams the synthetic dataset; mount EFS to replay the real CSV |

Public entry points (output `alb_dns_name`):

* `http://<alb>/` - dashboard
* `http://<alb>:8000/docs` - API (direct `/score` access for other systems)

## First-time setup

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # set github_repo and dashboard_password
terraform init
terraform apply
```

`dashboard_password` turns on HTTP basic auth for the whole dashboard (user
`dashboard_user`, default `owner`), including its `/api/` proxy. Leave it empty
only if something else (VPN, Cognito on the ALB) guards access. The file is
git-ignored because of it. With `enable_replay = true` the replay task
downloads the public dataset from `dataset_url` at start-up and streams it in a
loop, so the dashboard always shows live alerts.

Then in the GitHub repository settings:

| Kind | Name | Value (terraform output) |
|------|------|--------------------------|
| secret | `AWS_DEPLOY_ROLE_ARN` | `github_deploy_role_arn` |
| variable | `AWS_REGION` | your region |
| variable | `ECR_API_REPOSITORY` | `ecr_api_repository` |
| variable | `ECR_DASHBOARD_REPOSITORY` | `ecr_dashboard_repository` |
| variable | `ECS_CLUSTER` | `ecs_cluster_name` |
| variable | `ECS_API_SERVICE` | `ecs_api_service` |
| variable | `ECS_DASHBOARD_SERVICE` | `ecs_dashboard_service` |
| variable | `MODEL_BUCKET` | `model_bucket` |

Upload a trained model and ship:

```bash
aws s3 sync models/ s3://$(terraform output -raw model_bucket)/models/
git tag v0.1.0 && git push --tags        # runs .github/workflows/deploy.yml
```

The first `terraform apply` registers task definitions pointing at the `latest`
tag, which does not exist yet, so the services stay unhealthy until the first
deploy pushes images. That is expected.

## Day-2

* **Retrain and promote**: run `ml/train.py` (with feedback labels merged), review
  the metrics in `fraud_xgb.meta.json`, `aws s3 sync models/ s3://.../models/`,
  then tag a release. S3 versioning keeps every previous model for rollback.
* **Scale scoring**: raise `api_desired_count`; tasks join the same consumer
  group and share the stream.
* **Kafka instead of Redis**: point `FS_QUEUE_BACKEND=kafka` and
  `FS_KAFKA_BOOTSTRAP` at an Amazon MSK Serverless cluster (not provisioned here).
* **Hardening**: dedicated VPC with private subnets + NAT, HTTPS listener with
  an ACM certificate, Redis AUTH + TLS (`transit_encryption_enabled = true`
  and a `rediss://` URL), Cognito/ALB auth in front of the dashboard.

## Cost (rough, eu-west-1, on-demand)

| Item | ~USD / month |
|------|--------------|
| 2x Fargate 1 vCPU / 2 GB (api) | 70 |
| 1x Fargate 0.25 vCPU / 0.5 GB (dashboard) | 9 |
| ElastiCache cache.t4g.micro | 12 |
| RDS db.t4g.micro + 20 GB gp3 | 15 |
| ALB | 20 + traffic |
| ECR / S3 / CloudWatch | < 5 |
| **Total** | **~130** |

`terraform destroy` removes everything (the S3 bucket and ECR repos are set to
force-delete).
