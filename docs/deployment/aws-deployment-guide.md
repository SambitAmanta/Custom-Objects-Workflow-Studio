# Forge: Production AWS Deployment Guide

This guide provides end-to-end instructions for deploying Forge to Amazon Web Services (AWS) using **ECS Fargate**, **RDS PostgreSQL**, **MongoDB Atlas on AWS**, **AWS SQS**, **AWS S3**, and **CloudFront CDN**.

---

## 1. Cloud Architecture Overview

```
                          Internet / Clients
                                  │
         ┌────────────────────────┴────────────────────────┐
         ▼ (Route 53 DNS + HTTPS)                          ▼ (Route 53 DNS + HTTPS)
AWS CloudFront CDN (Global Edge)              Application Load Balancer (ALB)
  • S3 Static Hosting Origin                    • Public Subnets (AZ-a, AZ-b)
  • /api/* Dynamic Proxy Origin                 • SSL Certificate (ACM)
  • React 19 Web Studio (SPA)                   • Health check: /api/v1/schemas/types/
         │                                                 │
         │ (Proxy /api/* requests)                         ▼ (Port 8000)
         └────────────────────────────────────►  ECS Fargate: API Tasks (2+ Tasks)
                                                   • Django 5.1 / Gunicorn 22
                                                   • TenantRateThrottle (sliding-window)
                                                   • Private Subnets (No public IP)
                                                           │
                        ┌──────────────────────────────────┼──────────────────────────────────┐
                        ▼ (Multi-Doc Transactions)         ▼ (ORM Relational Storage)         ▼ (Atomic Thin Outbox)
             MongoDB Atlas on AWS (rs0)             AWS RDS PostgreSQL 16                 MongoDB outbox Collection
             • 3-node WiredTiger Replica Set         • Multi-AZ Private Subnet             • Claim: {$or: [pending, expired]}
             • collections: records, unique_keys     • auth_tenants, users, api_keys       • Lease duration: 30s
                        ▲                                                                     │
                        │                                                                     ▼
                        │                                                         ECS Fargate: Worker Tasks (1+ Task)
                        │                                                           • OutboxRelayWorker
                        │                                                           • Private Subnet via NAT Gateway
                        │                                                                     │
                        │                                                                     ▼ (event.version)
                        │                                                            AWS Standard SQS Queues
                        │                                                             • forge-prod-events-queue
                        │                                                             • forge-prod-events-dlq (Dead Letter)
                        │                                                                     │
                        ├─────────────────────────────────────────────────────────────────────┤
                        ▼                                                                     ▼
        SSRF-Hardened Webhook Dispatcher                                     CSV Streaming S3 Exporter
        • Anti-SSRF DNS Resolution & IP Pinning                               • Low-memory chunk streaming
        • ipaddress.is_global validation                                      • Deterministic S3 Key:
        • TLS SNI & Host preservation                                           tenants/{id}/exports/{export_id}.csv
        • HMAC-SHA256 timestamped signatures                                  • Role-masked CSV rows (FieldPermissionManager)
        • Leased deduplication ledger (processed_events)                       • Private encrypted S3 Bucket
```

---

## 2. Infrastructure Bill of Materials (BOM) & Costs

| Service | Resource | Sizing / Configuration | Estimated Monthly Cost |
| :--- | :--- | :--- | :--- |
| **Compute** | AWS ECS Fargate (API) | 2 tasks × 0.5 vCPU, 1 GB RAM | ~$15.00 / month |
| **Compute** | AWS ECS Fargate (Workers) | 1 task × 0.5 vCPU, 1 GB RAM | ~$7.50 / month |
| **Relational DB** | AWS RDS PostgreSQL 16 | `db.t4g.micro`, 20 GB gp3 storage | ~$13.50 / month (Free tier eligible) |
| **Document DB** | MongoDB Atlas on AWS | M0 Sandbox (Free) or M10 Dedicated | $0.00 (M0) / ~$57.00 (M10) |
| **Load Balancer** | Application Load Balancer | 1 ALB in 2 Availability Zones | ~$16.00 / month |
| **Networking** | NAT Gateway + VPC | 1 NAT Gateway + 2 Public / 2 Private subnets | ~$32.00 / month |
| **Messaging** | AWS Standard SQS + DLQ | Pay-per-request (first 1M free) | < $0.50 / month |
| **Storage** | AWS S3 + CloudFront CDN | CSV Exports bucket + Web Studio bucket | < $1.00 / month |
| **Secrets** | AWS SSM Parameter Store | Standard parameters | Free |
| **Total** | | **Development / Staging** | **~$0 – $35 / month** |
| **Total** | | **Production (Dedicated)** | **~$85 – $140 / month** |

---

## 3. Step-by-Step Deployment Walkthrough

### Phase 1: Prerequisites & Tooling
Ensure you have the following installed on your machine:
- **AWS CLI v2** (`aws --version`)
- **Terraform v1.5+** (`terraform -version`)
- **Docker** (`docker --version`)
- **Node.js 20+** & **Python 3.11+**

Authenticate the AWS CLI with administrative credentials:
```bash
aws configure
# Enter AWS Access Key ID, Secret Access Key, and default region (e.g. us-east-1)
aws sts get-caller-identity
```

---

### Phase 2: Database Provisioning

#### 1. MongoDB Atlas 7.0+ Replica Set
> [!IMPORTANT]
> MongoDB multi-document transactions strictly require a replica set (`rs0`).
1. Sign in to [MongoDB Atlas](https://www.mongodb.com/cloud/atlas).
2. Create a cluster in AWS `us-east-1` (M0 Free Tier or M10 Dedicated).
3. Under **Security > Database Access**:
   - Create user `forge_prod_user` with read and write access to `forge_tenant_data`.
4. Under **Security > Network Access**:
   - Add your NAT Gateway Elastic IP (or `0.0.0.0/0` during initial setup with strong password auth, then restrict to NAT IP once Terraform provisions).
5. Copy the standard connection string:
   ```
   mongodb+srv://forge_prod_user:<PASSWORD>@<CLUSTER_HOST>/?retryWrites=true&w=majority
   ```

#### 2. PostgreSQL 16 RDS (Automated via Terraform)
PostgreSQL is provisioned automatically in private subnets with an auto-generated 32-character master password via Terraform in Phase 3.

---

### Phase 3: Infrastructure Provisioning with Terraform

The directory `infra/terraform/` contains modular Terraform configurations:
- `vpc.tf`: Custom VPC (`10.0.0.0/16`), 2 public subnets, 2 private subnets, Internet Gateway, and NAT Gateway.
- `security_groups.tf`: Least-privilege ingress/egress rules between ALB, ECS API, ECS Workers, and RDS.
- `rds.tf`: AWS RDS PostgreSQL 16 instance (`db.t4g.micro`) with SSM parameter storage.
- `alb.tf`: Application Load Balancer with health checks on `/api/v1/schemas/types/`.
- `main.tf`: AWS Standard SQS Queue (`forge-prod-events-queue`), Dead Letter Queue (`forge-prod-events-dlq`), and encrypted S3 export bucket.
- `ecs.tf`: ECS Fargate cluster, task definitions, CloudWatch log streams, and IAM execution/task roles.
- `frontend.tf`: S3 bucket with CloudFront Origin Access Control (OAC), multi-origin routing (S3 static files + ALB `/api/*`), and SPA 404 rewrite rules.

#### Provisioning Commands:
```bash
cd infra/terraform

# 1. Initialize providers
terraform init

# 2. Plan deployment
terraform plan -out=tfplan \
  -var="environment=prod" \
  -var="aws_region=us-east-1" \
  -var="app_name=forge"

# 3. Apply infrastructure
terraform apply tfplan
```

Take note of the Terraform output values:
- `alb_dns_name`: e.g. `forge-prod-alb-123456789.us-east-1.elb.amazonaws.com`
- `cloudfront_domain_name`: e.g. `d123456abcdef8.cloudfront.net`
- `events_queue_url`: e.g. `https://sqs.us-east-1.amazonaws.com/<ACCOUNT_ID>/forge-prod-events-queue`
- `exports_bucket_name`: e.g. `forge-prod-csv-exports-us-east-1`
- `rds_endpoint`: e.g. `forge-prod-pg.c123456789.us-east-1.rds.amazonaws.com:5432`

---

### Phase 4: Configure Production Secrets in AWS SSM

Store production cryptographic secrets in AWS Systems Manager Parameter Store:

```bash
# 1. Django Secret Key (50+ random characters)
aws ssm put-parameter \
  --name "/forge/prod/DJANGO_SECRET_KEY" \
  --value "$(openssl rand -base64 48)" \
  --type SecureString --overwrite

# 2. Forge Server Secret (64 hex characters for HMAC-SHA256 uniqueness hashes)
aws ssm put-parameter \
  --name "/forge/prod/FORGE_SERVER_SECRET" \
  --value "$(openssl rand -hex 32)" \
  --type SecureString --overwrite

# 3. JWT Signing Key (32+ characters)
aws ssm put-parameter \
  --name "/forge/prod/JWT_SECRET_KEY" \
  --value "$(openssl rand -base64 36)" \
  --type SecureString --overwrite

# 4. MongoDB Atlas Connection URI
aws ssm put-parameter \
  --name "/forge/prod/MONGO_URI" \
  --value "mongodb+srv://forge_prod_user:<PASSWORD>@<ATLAS_HOST>/?retryWrites=true&w=majority" \
  --type SecureString --overwrite
```

---

### Phase 5: Container Build and ECR Deployment

#### 1. Create ECR Repositories:
```bash
aws ecr create-repository --repository-name forge-api --region us-east-1
aws ecr create-repository --repository-name forge-workers --region us-east-1
```

#### 2. Log In to ECR:
```bash
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text)
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin ${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com
```

#### 3. Build & Push API Image:
```bash
docker build -t forge-api:latest -f apps/api/Dockerfile .
docker tag forge-api:latest ${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/forge-api:latest
docker push ${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/forge-api:latest
```

#### 4. Build & Push Workers Image:
```bash
docker build -t forge-workers:latest -f apps/workers/Dockerfile .
docker tag forge-workers:latest ${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/forge-workers:latest
docker push ${AWS_ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/forge-workers:latest
```

---

### Phase 6: Run One-Off Migrations & Bootstrap Data

Execute a standalone ECS Fargate task to apply relational database migrations and seed the initial tenant structure:

```bash
SUBNET_ID=$(terraform output -json private_subnet_ids | jq -r '.[0]')
SEC_GROUP=$(terraform output -json ecs_api_security_group_id | jq -r '.')

aws ecs run-task \
  --cluster forge-prod-cluster \
  --task-definition forge-prod-api \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[\"${SUBNET_ID}\"],securityGroups=[\"${SEC_GROUP}\"],assignPublicIp=\"DISABLED\"}" \
  --overrides '{"containerOverrides": [{"name": "api", "command": ["python", "scripts/seed_demo.py"]}]}'
```

Monitor migration logs in CloudWatch:
```bash
aws logs tail /ecs/forge-prod-api --follow
```

---

### Phase 7: Deploy Frontend to S3 + CloudFront CDN

1. Build the production React application with the CloudFront API endpoint:
   ```bash
   cd apps/web
   npm ci
   npm run build
   cd ../..
   ```

2. Sync static assets to the private S3 bucket:
   ```bash
   aws s3 sync apps/web/dist s3://forge-prod-studio-web --delete
   ```

3. Invalidate CloudFront cache:
   ```bash
   DIST_ID=$(aws cloudfront list-distributions --query "DistributionList.Items[?Aliases.Items!=null && contains(Origins.Items[0].DomainName, 'forge-prod-studio-web')].Id" --output text)
   aws cloudfront create-invalidation --distribution-id $DIST_ID --paths "/*"
   ```

---

### Phase 8: Verification & Health Check

1. **Verify API Endpoint**:
   ```bash
   curl -i http://<ALB_DNS_NAME>/api/v1/schemas/types/
   # Expect HTTP 401 Unauthorized (proving authentication middleware and API are live)
   ```

2. **Verify Web Studio in Browser**:
   Open `https://<CLOUDFRONT_DOMAIN_NAME>` in your browser.
   - Log in with provisioned credentials: `alice@acme.com` / `password123`.
   - Create an invoice custom object, verify ETag optimistic concurrency, and observe real-time field masking.

3. **Verify Outbox Relay & SQS Dispatching**:
   Inspect CloudWatch logs for the worker task:
   ```bash
   aws logs tail /ecs/forge-prod-workers --follow
   ```
   You should see:
   ```
   [forge.workers] Forge Worker runner started (service=all, poll_interval=1.0s)
   [forge.workers] Relay published 1 event(s) to SQS
   [forge.workers] Webhook consumer processed 1 message(s)
   ```

---

## 4. Disaster Recovery & Operational Runbooks

### SQS Dead Letter Queue (DLQ) Redrive
If webhooks fail repeatedly (HTTP 500 or timeout), they route to `forge-prod-events-dlq`:
```bash
# Check DLQ message count
aws sqs get-queue-attributes \
  --queue-url $(terraform output -raw events_dlq_url) \
  --attribute-names ApproximateNumberOfMessages

# Redrive messages back to the primary queue once endpoints are healthy
aws sqs start-message-move-task \
  --source-arn $(terraform output -raw events_dlq_arn) \
  --destination-arn $(terraform output -raw events_queue_arn)
```

### ECS Service Scaling
Scale API containers horizontally during load spikes:
```bash
aws ecs update-service \
  --cluster forge-prod-cluster \
  --service forge-prod-api-service \
  --desired-count 5
```

### Rolling Deployment
To push new code without downtime:
```bash
# Force new deployment pulling latest ECR image
aws ecs update-service \
  --cluster forge-prod-cluster \
  --service forge-prod-api-service \
  --force-new-deployment
```
