variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "app_name" {
  description = "Application name"
  type        = string
  default     = "forge"
}

variable "ecr_api_image_url" {
  description = "ECR Repository URL for Forge API image"
  type        = string
  default     = "forge-api"
}

variable "ecr_workers_image_url" {
  description = "ECR Repository URL for Forge Workers image"
  type        = string
  default     = "forge-workers"
}

