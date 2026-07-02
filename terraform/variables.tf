# ── Variables ─────────────────────────────────────────────────────────────────
variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
  default     = "latency-probe-cluster"
}

variable "ecr_repo_name" {
  description = "ECR repository name for the latency-probe image"
  type        = string
  default     = "latency-probe"
}
