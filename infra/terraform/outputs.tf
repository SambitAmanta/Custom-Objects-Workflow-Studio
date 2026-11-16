output "events_queue_url" {
  description = "URL of the primary SQS outbox events queue"
  value       = aws_sqs_queue.events_queue.url
}

output "events_queue_arn" {
  description = "ARN of the primary SQS outbox events queue"
  value       = aws_sqs_queue.events_queue.arn
}

output "events_dlq_url" {
  description = "URL of the SQS Dead-Letter Queue"
  value       = aws_sqs_queue.events_dlq.url
}

output "exports_bucket_name" {
  description = "Name of the S3 bucket for CSV exports"
  value       = aws_s3_bucket.exports_bucket.id
}

output "alb_dns_name" {
  description = "Public DNS name of the Application Load Balancer"
  value       = aws_lb.api.dns_name
}

output "cloudfront_domain_name" {
  description = "Domain name of the CloudFront CDN distribution"
  value       = aws_cloudfront_distribution.cdn.domain_name
}

output "rds_endpoint" {
  description = "Connection endpoint of the PostgreSQL RDS instance"
  value       = aws_db_instance.postgres.endpoint
}

output "ecs_cluster_name" {
  description = "Name of the ECS Fargate cluster"
  value       = aws_ecs_cluster.main.name
}

