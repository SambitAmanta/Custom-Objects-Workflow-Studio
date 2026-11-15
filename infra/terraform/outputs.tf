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

