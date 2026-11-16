# AWS ECS Fargate Cluster, Task Definitions, and Services

resource "aws_ecs_cluster" "main" {
  name = "${var.app_name}-${var.environment}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Name        = "${var.app_name}-${var.environment}-cluster"
    Environment = var.environment
  }
}

# CloudWatch Log Groups
resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${var.app_name}-${var.environment}-api"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "workers" {
  name              = "/ecs/${var.app_name}-${var.environment}-workers"
  retention_in_days = 30
}

# IAM Task Execution Role (allows ECS agent to pull ECR images and send logs)
resource "aws_iam_role" "ecs_execution_role" {
  name = "${var.app_name}-${var.environment}-ecs-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution_standard" {
  role       = aws_iam_role.ecs_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Allow task execution role to read SSM Parameters
resource "aws_iam_policy" "ssm_read_policy" {
  name = "${var.app_name}-${var.environment}-ssm-read"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameters",
          "ssm:GetParameter",
          "ssm:GetParametersByPath"
        ]
        Resource = "arn:aws:ssm:${var.aws_region}:*:parameter/${var.app_name}/${var.environment}/*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution_ssm" {
  role       = aws_iam_role.ecs_execution_role.name
  policy_arn = aws_iam_policy.ssm_read_policy.arn
}

# IAM Task Role (application runtime permissions: SQS, S3, SSM)
resource "aws_iam_role" "ecs_task_role" {
  name = "${var.app_name}-${var.environment}-ecs-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_policy" "task_app_policy" {
  name = "${var.app_name}-${var.environment}-task-app-policy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sqs:SendMessage",
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]
        Resource = [
          aws_sqs_queue.events_queue.arn,
          aws_sqs_queue.events_dlq.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.exports_bucket.arn,
          "${aws_s3_bucket.exports_bucket.arn}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_app_attach" {
  role       = aws_iam_role.ecs_task_role.name
  policy_arn = aws_iam_policy.task_app_policy.arn
}

# 1. API Task Definition & Service
resource "aws_ecs_task_definition" "api" {
  family                   = "${var.app_name}-${var.environment}-api"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = "${var.ecr_api_image_url}:latest"
      essential = true

      portMappings = [
        {
          containerPort = 8000
          hostPort      = 8000
        }
      ]

      environment = [
        { name = "DJANGO_DEBUG", value = "False" },
        { name = "DJANGO_ALLOWED_HOSTS", value = "*" },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "SQS_OUTBOX_QUEUE_URL", value = aws_sqs_queue.events_queue.id },
        { name = "SQS_DLQ_QUEUE_URL", value = aws_sqs_queue.events_dlq.id },
        { name = "S3_EXPORT_BUCKET_NAME", value = aws_s3_bucket.exports_bucket.id }
      ]

      secrets = [
        { name = "DATABASE_URL", valueFrom = aws_ssm_parameter.database_url.arn },
        { name = "MONGO_URI", valueFrom = "arn:aws:ssm:${var.aws_region}:*:parameter/${var.app_name}/${var.environment}/MONGO_URI" },
        { name = "DJANGO_SECRET_KEY", valueFrom = "arn:aws:ssm:${var.aws_region}:*:parameter/${var.app_name}/${var.environment}/DJANGO_SECRET_KEY" },
        { name = "FORGE_SERVER_SECRET", valueFrom = "arn:aws:ssm:${var.aws_region}:*:parameter/${var.app_name}/${var.environment}/FORGE_SERVER_SECRET" },
        { name = "JWT_SECRET_KEY", valueFrom = "arn:aws:ssm:${var.aws_region}:*:parameter/${var.app_name}/${var.environment}/JWT_SECRET_KEY" }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.api.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "api"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "api" {
  name            = "${var.app_name}-${var.environment}-api-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = 2
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = [aws_subnet.private_1.id, aws_subnet.private_2.id]
    security_groups  = [aws_security_group.ecs_api.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.http]
}

# 2. Background Workers Task Definition & Service
resource "aws_ecs_task_definition" "workers" {
  family                   = "${var.app_name}-${var.environment}-workers"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "workers"
      image     = "${var.ecr_workers_image_url}:latest"
      essential = true

      environment = [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "SQS_OUTBOX_QUEUE_URL", value = aws_sqs_queue.events_queue.id },
        { name = "SQS_DLQ_QUEUE_URL", value = aws_sqs_queue.events_dlq.id },
        { name = "S3_EXPORT_BUCKET_NAME", value = aws_s3_bucket.exports_bucket.id }
      ]

      secrets = [
        { name = "DATABASE_URL", valueFrom = aws_ssm_parameter.database_url.arn },
        { name = "MONGO_URI", valueFrom = "arn:aws:ssm:${var.aws_region}:*:parameter/${var.app_name}/${var.environment}/MONGO_URI" },
        { name = "DJANGO_SECRET_KEY", valueFrom = "arn:aws:ssm:${var.aws_region}:*:parameter/${var.app_name}/${var.environment}/DJANGO_SECRET_KEY" },
        { name = "FORGE_SERVER_SECRET", valueFrom = "arn:aws:ssm:${var.aws_region}:*:parameter/${var.app_name}/${var.environment}/FORGE_SERVER_SECRET" },
        { name = "JWT_SECRET_KEY", valueFrom = "arn:aws:ssm:${var.aws_region}:*:parameter/${var.app_name}/${var.environment}/JWT_SECRET_KEY" }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.workers.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "workers"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "workers" {
  name            = "${var.app_name}-${var.environment}-workers-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.workers.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = [aws_subnet.private_1.id, aws_subnet.private_2.id]
    security_groups  = [aws_security_group.ecs_workers.id]
    assign_public_ip = false
  }
}

