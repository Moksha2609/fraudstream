# fraudstream on AWS: ECS Fargate (api + dashboard [+ replay]), ElastiCache
# Redis Streams, RDS Postgres, ALB, ECR, S3 model bucket, GitHub OIDC deploys.
#
# Networking is kept to the account's default VPC to stay short. For a real
# production footprint, swap the data sources below for a dedicated VPC with
# private subnets and NAT, and move the ECS tasks off public IPs.

data "aws_caller_identity" "current" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

locals {
  name       = var.name
  account_id = data.aws_caller_identity.current.account_id
  registry   = "${local.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com"
  subnet_ids = data.aws_subnets.default.ids

  # Cloud Map / Service Connect namespace. The dashboard's nginx proxies
  # /api/ -> http://api:8000/ ; Service Connect resolves the short name "api".
  namespace = "${local.name}.local"

  redis_url    = "redis://${aws_elasticache_replication_group.redis.primary_endpoint_address}:6379/0"
  database_url = "postgresql+psycopg://${aws_db_instance.postgres.username}:${random_password.db.result}@${aws_db_instance.postgres.address}:5432/${aws_db_instance.postgres.db_name}"

  base_env = [
    { name = "FS_QUEUE_BACKEND", value = "redis" },
    { name = "FS_REDIS_URL", value = local.redis_url },
    { name = "FS_TX_TOPIC", value = "transactions" },
    { name = "FS_ALERT_TOPIC", value = "alerts" },
    { name = "FS_CONSUMER_GROUP", value = "scorer" },
    { name = "FS_MODEL_DIR", value = "/models" },
    { name = "FS_MODEL_NAME", value = "fraud_xgb" },
    { name = "FS_DRIFT_PSI_ALERT", value = tostring(var.drift_psi_alert) },
    { name = "FS_LOG_LEVEL", value = "info" },
  ]

  threshold_env = var.alert_threshold == "" ? [] : [
    { name = "FS_ALERT_THRESHOLD", value = var.alert_threshold },
  ]

  backend_env = concat(local.base_env, local.threshold_env)

  backend_secrets = [
    { name = "FS_DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn },
  ]
}

# ---------------------------------------------------------------------------
# Container registry + model bucket
# ---------------------------------------------------------------------------
resource "aws_ecr_repository" "api" {
  name                 = "${local.name}/api"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "dashboard" {
  name                 = "${local.name}/dashboard"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "keep_recent" {
  for_each   = { api = aws_ecr_repository.api.name, dashboard = aws_ecr_repository.dashboard.name }
  repository = each.value

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep last 20 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 20 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_s3_bucket" "models" {
  bucket_prefix = "${local.name}-models-"
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "models" {
  bucket = aws_s3_bucket.models.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "models" {
  bucket                  = aws_s3_bucket.models.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------------------
# Security groups
# ---------------------------------------------------------------------------
resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Public ALB: dashboard on 80, API on 8000"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "ecs" {
  name        = "${local.name}-ecs"
  description = "ECS tasks: reachable from the ALB and from each other"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    from_port       = 80
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    from_port = 0
    to_port   = 0
    protocol  = "-1"
    self      = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "redis" {
  name        = "${local.name}-redis"
  description = "ElastiCache: only from ECS tasks"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "rds" {
  name        = "${local.name}-rds"
  description = "RDS Postgres: only from ECS tasks"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ---------------------------------------------------------------------------
# Redis Streams (ElastiCache) and Postgres (RDS)
# ---------------------------------------------------------------------------
resource "aws_elasticache_subnet_group" "redis" {
  name       = "${local.name}-redis"
  subnet_ids = local.subnet_ids
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "${local.name}-redis"
  description          = "fraudstream transaction + alert streams"
  engine               = "redis"
  engine_version       = "7.1"
  node_type            = var.redis_node_type
  num_cache_clusters   = 1
  port                 = 6379
  parameter_group_name = "default.redis7"
  subnet_group_name    = aws_elasticache_subnet_group.redis.name
  security_group_ids   = [aws_security_group.redis.id]

  # plain redis:// to keep the URL simple; enable TLS + AUTH for a hardened setup
  at_rest_encryption_enabled = true
  transit_encryption_enabled = false
  automatic_failover_enabled = false
  apply_immediately          = true
}

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "postgres" {
  name       = "${local.name}-db"
  subnet_ids = local.subnet_ids
}

resource "aws_db_instance" "postgres" {
  identifier              = "${local.name}-db"
  engine                  = "postgres"
  engine_version          = "16"
  instance_class          = var.db_instance_class
  allocated_storage       = var.db_allocated_storage
  storage_type            = "gp3"
  db_name                 = "fraudstream"
  username                = "fraud"
  password                = random_password.db.result
  db_subnet_group_name    = aws_db_subnet_group.postgres.name
  vpc_security_group_ids  = [aws_security_group.rds.id]
  publicly_accessible     = false
  skip_final_snapshot     = true
  backup_retention_period = 7
  storage_encrypted       = true
  apply_immediately       = true
}

resource "aws_secretsmanager_secret" "database_url" {
  name_prefix             = "${local.name}/database_url-"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id     = aws_secretsmanager_secret.database_url.id
  secret_string = local.database_url
}

# ---------------------------------------------------------------------------
# IAM
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "${local.name}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "task_execution_managed" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "task_execution_secrets" {
  name = "read-secrets"
  role = aws_iam_role.task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [aws_secretsmanager_secret.database_url.arn]
    }]
  })
}

# The application itself needs no AWS API access today; the role exists so
# permissions (e.g. S3 model reads) can be added without touching task defs.
resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "task_models_read" {
  name = "read-models"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:ListBucket"]
      Resource = [aws_s3_bucket.models.arn, "${aws_s3_bucket.models.arn}/*"]
    }]
  })
}

# GitHub Actions deploys through OIDC - no static keys.
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:*"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = "${local.name}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_assume.json
}

resource "aws_iam_role_policy" "github_deploy" {
  name = "deploy"
  role = aws_iam_role.github_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrLogin"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "EcrPush"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
        ]
        Resource = [aws_ecr_repository.api.arn, aws_ecr_repository.dashboard.arn]
      },
      {
        Sid    = "EcsDeploy"
        Effect = "Allow"
        Action = [
          "ecs:DescribeTaskDefinition", "ecs:RegisterTaskDefinition",
          "ecs:DescribeServices", "ecs:UpdateService",
          "elasticloadbalancing:DescribeLoadBalancers",
        ]
        Resource = "*"
      },
      {
        Sid      = "PassTaskRoles"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.task_execution.arn, aws_iam_role.task.arn]
      },
      {
        Sid      = "ReadModels"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = [aws_s3_bucket.models.arn, "${aws_s3_bucket.models.arn}/*"]
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Logs, cluster, service discovery
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${local.name}/api"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "dashboard" {
  name              = "/ecs/${local.name}/dashboard"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "replay" {
  name              = "/ecs/${local.name}/replay"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_cluster" "main" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

resource "aws_service_discovery_http_namespace" "main" {
  name        = local.namespace
  description = "Service Connect namespace for fraudstream"
}

# ---------------------------------------------------------------------------
# Load balancer
# ---------------------------------------------------------------------------
resource "aws_lb" "main" {
  name               = local.name
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = local.subnet_ids
}

resource "aws_lb_target_group" "dashboard" {
  name        = "${local.name}-dashboard"
  port        = 80
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = data.aws_vpc.default.id

  health_check {
    path                = "/healthz"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_target_group" "api" {
  name        = "${local.name}-api"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = data.aws_vpc.default.id

  health_check {
    path                = "/health"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

# Port 80: dashboard. Its nginx proxies /api/* to the API over Service Connect,
# because an ALB cannot strip the /api prefix the API does not expect.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.dashboard.arn
  }
}

# Port 8000: the API itself (OpenAPI docs, direct /score calls from other systems).
resource "aws_lb_listener" "api" {
  load_balancer_arn = aws_lb.main.arn
  port              = 8000
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

# ---------------------------------------------------------------------------
# Task definitions
# ---------------------------------------------------------------------------
resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name      = "api"
    image     = "${aws_ecr_repository.api.repository_url}:${var.api_image_tag}"
    essential = true
    portMappings = [{
      name          = "api"
      containerPort = 8000
      protocol      = "tcp"
      appProtocol   = "http"
    }]
    environment = concat(local.backend_env, [
      { name = "FS_RUN_CONSUMER", value = "true" },
      { name = "FS_API_HOST", value = "0.0.0.0" },
      { name = "FS_API_PORT", value = "8000" },
      { name = "FS_CORS_ORIGINS", value = "*" },
    ])
    secrets = local.backend_secrets
    healthCheck = {
      command     = ["CMD-SHELL", "curl -fsS http://127.0.0.1:8000/health || exit 1"]
      interval    = 15
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.api.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "api"
      }
    }
  }])

  lifecycle {
    # deploys register new revisions with new image tags; don't fight them
    ignore_changes = [container_definitions]
  }
}

resource "aws_ecs_task_definition" "dashboard" {
  family                   = "${local.name}-dashboard"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name      = "dashboard"
    image     = "${aws_ecr_repository.dashboard.repository_url}:${var.dashboard_image_tag}"
    essential = true
    environment = [
      { name = "DASHBOARD_USER", value = var.dashboard_user },
      { name = "DASHBOARD_PASSWORD", value = var.dashboard_password },
    ]
    portMappings = [{
      name          = "dashboard"
      containerPort = 80
      protocol      = "tcp"
      appProtocol   = "http"
    }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.dashboard.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "dashboard"
      }
    }
  }])

  lifecycle {
    ignore_changes = [container_definitions]
  }
}

# Demo producer. The image has no dataset baked in, so the task downloads the
# public dataset (var.dataset_url, ~150 MB) into ephemeral storage at start-up
# and then replays it in a loop so the dashboard always has live alerts.
resource "aws_ecs_task_definition" "replay" {
  count                    = var.enable_replay ? 1 : 0
  family                   = "${local.name}-replay"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "replay"
    image     = "${aws_ecr_repository.api.repository_url}:${var.api_image_tag}"
    essential = true
    command = ["sh", "-c", join(" ", [
      "curl -fsSL --retry 5 -o /tmp/creditcard.csv '${var.dataset_url}' &&",
      "fraudstream-replay --data /tmp/creditcard.csv --speed ${var.replay_speed} --loop",
    ])]
    environment = local.backend_env
    secrets     = local.backend_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.replay.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "replay"
      }
    }
  }])
}

# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------
resource "aws_ecs_service" "api" {
  name            = "${local.name}-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  deployment_minimum_healthy_percent = 50
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 60
  enable_execute_command             = true

  network_configuration {
    subnets          = local.subnet_ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = true # default VPC has no NAT; needed to pull from ECR
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.main.arn

    service {
      port_name      = "api"
      discovery_name = "api"

      client_alias {
        port     = 8000
        dns_name = "api"
      }
    }
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    ignore_changes = [task_definition]
  }

  depends_on = [aws_lb_listener.api]
}

resource "aws_ecs_service" "dashboard" {
  name            = "${local.name}-dashboard"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.dashboard.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  health_check_grace_period_seconds = 30

  network_configuration {
    subnets          = local.subnet_ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.dashboard.arn
    container_name   = "dashboard"
    container_port   = 80
  }

  # client-only membership: lets nginx resolve http://api:8000
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.main.arn
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    ignore_changes = [task_definition]
  }

  depends_on = [aws_lb_listener.http, aws_ecs_service.api]
}

resource "aws_ecs_service" "replay" {
  count           = var.enable_replay ? 1 : 0
  name            = "${local.name}-replay"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.replay[0].arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = local.subnet_ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = true
  }

  depends_on = [aws_ecs_service.api]
}
