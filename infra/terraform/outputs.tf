output "alb_dns_name" {
  description = "Dashboard at http://<dns>/ ; API directly at http://<dns>:8000/"
  value       = aws_lb.main.dns_name
}

output "ecr_api_repository" {
  value = aws_ecr_repository.api.name
}

output "ecr_dashboard_repository" {
  value = aws_ecr_repository.dashboard.name
}

output "ecr_registry" {
  value = split("/", aws_ecr_repository.api.repository_url)[0]
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "ecs_api_service" {
  value = aws_ecs_service.api.name
}

output "ecs_dashboard_service" {
  value = aws_ecs_service.dashboard.name
}

output "github_deploy_role_arn" {
  description = "Set as secrets.AWS_DEPLOY_ROLE_ARN in GitHub"
  value       = aws_iam_role.github_deploy.arn
}

output "model_bucket" {
  description = "Upload approved models here: aws s3 sync models/ s3://<bucket>/models/"
  value       = aws_s3_bucket.models.bucket
}

output "redis_endpoint" {
  value = aws_elasticache_replication_group.redis.primary_endpoint_address
}

output "database_endpoint" {
  value = aws_db_instance.postgres.address
}
