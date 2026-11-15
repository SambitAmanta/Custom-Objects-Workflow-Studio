terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# 1. Dead-Letter Queue (DLQ)
resource "aws_sqs_queue" "events_dlq" {
  name                      = "${var.app_name}-${var.environment}-events-dlq"
  message_retention_seconds = 1209600 # 14 days
}

# 2. Primary Standard SQS Queue for Outbox Events with Redrive Policy
resource "aws_sqs_queue" "events_queue" {
  name                      = "${var.app_name}-${var.environment}-events-queue"
  visibility_timeout_seconds = 60
  message_retention_seconds = 86400 # 1 day

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.events_dlq.arn
    maxReceiveCount     = 5
  })
}

# 3. S3 Bucket for CSV Exports
resource "aws_s3_bucket" "exports_bucket" {
  bucket = "${var.app_name}-${var.environment}-csv-exports-${var.aws_region}"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "exports_encryption" {
  bucket = aws_s3_bucket.exports_bucket.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "exports_block_public" {
  bucket = aws_s3_bucket.exports_bucket.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

