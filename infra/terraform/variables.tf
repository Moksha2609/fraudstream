variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "eu-west-1"
}

variable "name" {
  description = "Base name for all resources"
  type        = string
  default     = "fraudstream"
}

variable "github_repo" {
  description = "GitHub repository allowed to deploy via OIDC, as owner/name"
  type        = string
}

variable "api_image_tag" {
  description = "Initial image tag for the API service (deploys update it afterwards)"
  type        = string
  default     = "latest"
}

variable "dashboard_image_tag" {
  description = "Initial image tag for the dashboard service"
  type        = string
  default     = "latest"
}

variable "api_desired_count" {
  description = "Number of API tasks (each also runs a queue consumer)"
  type        = number
  default     = 2
}

variable "api_cpu" {
  type    = number
  default = 1024
}

variable "api_memory" {
  type    = number
  default = 2048
}

variable "enable_replay" {
  description = "Run a replay task that streams the dataset into the queue (demo only)"
  type        = bool
  default     = false
}

variable "replay_speed" {
  description = "Replay speed multiplier for the demo replay task"
  type        = number
  default     = 120
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_allocated_storage" {
  type    = number
  default = 20
}

variable "alert_threshold" {
  description = "Optional FS_ALERT_THRESHOLD override; empty keeps the model's stored threshold"
  type        = string
  default     = ""
}

variable "drift_psi_alert" {
  type    = number
  default = 0.2
}

variable "log_retention_days" {
  type    = number
  default = 30
}
