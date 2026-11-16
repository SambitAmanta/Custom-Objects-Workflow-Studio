# Forge: AWS Architecture & Deployment Guide

> [!NOTE]
> **Architecture & Deployment Scope**: This guide documents two architectures:
> 1. An **Enterprise Reference Architecture** (multi-tier ECS Fargate, ALB, Multi-AZ RDS, and CloudFront declared in `infra/terraform/`, reference blueprint only / not actively deployed to AWS to prevent ongoing cloud hosting charges).
> 2. A **Cost-Optimized Single-Instance Deployment** (~$15–$20/mo via EC2 + Docker Compose + Caddy + MongoDB Atlas M0 + real AWS SQS/S3 via IAM roles).
>
> In local development and CI testing, Forge runs against LocalStack (SQS/S3) and Docker Compose. Do not claim live production AWS hosting until an environment is actually provisioned.

---

## 1. Enterprise Reference Architecture (Multi-Tier ECS Fargate Blueprint - Reference Only)

The multi-tier blueprint decouples the presentation layer, control plane, event consumers, and state stores into independent AWS managed primitives:

```
                          Internet / Clients
                                  │
                                  ▼ (Route 53 DNS + HTTPS)
                  AWS CloudFront CDN (Unified Global Edge)
                    • S3 Static Hosting Origin (React 19 Web Studio SPA)
                    • Dynamic /api/* Proxy Origin (Routes to ALB)
                    • SSL/TLS Termination & DDoS Mitigation
                                  │
                                  ▼ (Proxy /api/* requests)
                  Application Load Balancer (ALB)
                    • Public Subnets across 2 AZs (AZ-a, AZ-b)
                    • Unauthenticated Health Check: /healthz (200 OK)
                                  │
                                  ▼ (Port 8000 via Private Subnets)
                  ECS Fargate: API Tasks (2+ Tasks)
                    • Django 5.1 / Gunicorn 22 in private subnets (no public IP)
                    • TenantRateThrottle sliding-window protection
                                  │
         ┌────────────────────────┼────────────────────────┐
         ▼ (Multi-Doc Txns)       ▼ (Relational Storage)   ▼ (Atomic Thin Outbox)
MongoDB Atlas (rs0)      AWS RDS PostgreSQL 16    MongoDB outbox Collection
• 3-node Replica Set     • Multi-AZ Private Subnet • Leased claim (30s lease)
• records, unique_keys   • auth_tenants, users     • Complete marking
         ▲                                                 │
         │                                                 ▼
         │                                     ECS Fargate: Worker Tasks (1+ Task)
         │                                       • OutboxRelayWorker (1s poll)
         │                                       • Private Subnet via NAT Gateway
         │                                                 │
         │                                                 ▼ (Thin Event payload)
         │                                        AWS Standard SQS Queues
         │                                         • forge-prod-events-queue
         │                                         • forge-prod-events-dlq (Dead Letter)
         │                                                 │
         ├─────────────────────────────────────────────────┤
         ▼                                                 ▼
SSRF-Hardened Webhook Dispatcher                  CSV Streaming S3 Exporter
• ipaddress.is_global validation                   • Low-memory chunk streaming
• IP Pinning & TLS SNI preservation                • Deterministic S3 Key:
• Leased deduplication ledger                        tenants/{id}/exports/{export_id}.csv
• HMAC-SHA256 signatures                          • Role-masked CSV rows (FieldPermissionManager)
• Exponential backoff & DLQ routing                • Private KMS-encrypted S3 Bucket
```

---

## 2. Infrastructure Bill of Materials (BOM) & Cost Breakdown

### Enterprise Multi-Tier Blueprint (Real AWS Pricing — us-east-1)

AWS pricing requires accounting for compute, network gateways, load balancer base hours, public IPv4 address surcharges, and database multi-AZ replication:

| Service | Resource | Sizing & Pricing Basis | Estimated Monthly Cost |
| :--- | :--- | :--- | :--- |
| **Compute (API)** | AWS ECS Fargate | 2 tasks × 0.5 vCPU, 1 GB RAM (x86: $0.04048/vCPU-hr, $0.004445/GB-hr) | ~$36.00 / month (x86)<br>*(~$28.80 / mo on ARM64)* |
| **Compute (Workers)** | AWS ECS Fargate | 1 task × 0.5 vCPU, 1 GB RAM | ~$18.00 / month (x86)<br>*(~$14.40 / mo on ARM64)* |
| **Relational DB** | AWS RDS PostgreSQL 16 | `db.t4g.micro` Multi-AZ ($0.032/hr × 730) + 20 GB gp3 ($2.30) | ~$29.30 / month |
| **Document DB** | MongoDB Atlas on AWS | M0 Sandbox (Free, 512 MB, 100 ops/s) or M10 Dedicated | $0.00 (M0) / ~$57.00 (M10) |
| **Load Balancer** | Application Load Balancer | 1 ALB in 2 AZs ($0.0225/hr × 730 hrs) + LCU | ~$16.43 / month |
| **Networking** | AWS NAT Gateway | 1 NAT Gateway ($0.045/hr × 730 hrs) + data processing | ~$32.85 / month base |
| **IPv4 Addresses** | Public IPv4 Surcharge | 1 NAT Elastic IP + 2 ALB public IPs (3 × $0.005/hr × 730 hrs) | ~$10.95 / month |
| **Messaging** | AWS Standard SQS + DLQ | Pay-per-request (first 1,000,000 requests free) | < $0.50 / month |
| **Storage & Edge** | AWS S3 + CloudFront CDN | S3 storage + CloudFront CDN (first 1 TB data transfer free) | < $1.50 / month |
| **Secrets** | AWS SSM Parameter Store | Standard SecureString parameters | Free |
| **Total (with M0 Atlas)** | | **Enterprise Stack with Free Tier Mongo** | **~$125 – $145 / month** |
| **Total (with M10 Atlas)**| | **Enterprise Stack with Dedicated Mongo** | **~$180 – $200 / month** |

> [!WARNING]
> **AWS Account Free Tier & Credit Model (Post-July 2025)**:
> AWS accounts created after July 15, 2025 operate on a credits-based plan ($100 initial credits plus activity bonuses, valid for 6 months). Running the enterprise multi-tier stack (~$125–$145/month) will consume your entire credit allocation in under two months. Furthermore, adding the account to an AWS Organization forfeits the Free plan guardrails and transitions to direct billing.

---

## 3. Cost-Optimized Single-Instance Deployment Option (~$15–$20 / month)

For personal hosting, recruiter demos, or testing real AWS integrations without burning cloud budgets, use the single-instance architecture:

### Architecture Specification
- **Host**: 1 AWS EC2 instance:
  - `t4g.small` (ARM64 Graviton2, 2 vCPU, 2 GB RAM): **$0.0168/hr** (~$12.26 / month).
  - Or `t3.small` (x86, 2 vCPU, 2 GB RAM): **$0.0208/hr** (~$15.18 / month).
  - 1 Public IPv4 address ($0.005/hr × 730 = ~$3.65 / month).
  - 20 GB gp3 root EBS volume ($0.08/GB = ~$1.60 / month).
  - **Total Host Cost**: **~$17.50 – $20.40 / month** (100% covered by 6-month starter credits).
- **Workloads on Instance (via Docker Compose)**:
  - `api`: Django 5.1 / Gunicorn control plane & REST API.
  - `workers`: Outbox relay, SQS webhook consumer, and S3 CSV exporter.
  - `postgres`: PostgreSQL 16 container with local EBS persistence.
  - `caddy`: Reverse proxy providing:
    - Automatic TLS/SSL certificates via Let's Encrypt using `sslip.io` (e.g., `forge.<PUBLIC_IP>.sslip.io`) without purchasing a custom domain.
    - Reverse proxies `/api/*` to Django on `localhost:8000`.
    - Directly serves pre-built static React 19 Web Studio SPA files (`dist/`).
- **Managed Document DB**: MongoDB Atlas M0 Free Tier (3-node replica set `rs0`, 512 MB storage, 100 ops/s cap).
- **Real AWS Services via EC2 IAM Instance Profile**:
  - AWS Standard SQS (`forge-prod-events-queue`) and DLQ (`forge-prod-events-dlq`).
  - AWS S3 private bucket for CSV exports (`forge-prod-csv-exports`).
  - EC2 IAM instance profile attaches least-privilege permissions directly to the instance, eliminating hardcoded access keys on disk.
- **Critical Security & Cost Guardrails**:
  - **IMDSv2 Enforced**: Run with hop limit 1 to prevent SSRF exfiltration of IAM instance credentials:
    ```bash
    aws ec2 modify-instance-metadata-options \
      --instance-id <INSTANCE_ID> \
      --http-tokens required \
      --http-put-response-hop-limit 1
    ```
  - **SSRF Blocklist**: Forge's SSRF validator in `apps/workers/consumers.py` explicitly rejects `169.254.169.254` and private ranges (`ipaddress.is_global`).
  - **AWS Budgets Alert**: Create a budget alert at **$5.00** and **$15.00** to notify you immediately of unexpected charges.
  - **Atlas M0 Warmup**: Free Atlas clusters sleep when idle; send an HTTP request to wake up the cluster 60 seconds before live demos.

---

## 4. Step-by-Step Deployment Walkthrough (Enterprise Multi-Tier)

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
- `alb.tf`: Application Load Balancer with unauthenticated liveness health check on `/healthz` (matcher: `200`).
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

1. **Verify Unauthenticated Liveness Probe (`/healthz`)**:
   ```bash
   curl -i http://<ALB_DNS_NAME>/healthz
   # Expect HTTP 200 OK: {"status": "ok", "service": "forge-api"}
   ```

2. **Verify Authenticated & Tenant-Scoped Middleware**:
   ```bash
   curl -i http://<ALB_DNS_NAME>/api/v1/schemas/types/
   # Expect HTTP 401 Unauthorized (proving authentication middleware and tenant filters are active)
   ```

3. **Verify Web Studio in Browser**:
   Open `https://<CLOUDFRONT_DOMAIN_NAME>` in your browser.
   - Log in with provisioned credentials: `alice@acme.com` / `password123`.
   - Create an invoice custom object, verify ETag optimistic concurrency, and observe real-time field masking.

4. **Verify Outbox Relay & SQS Dispatching**:
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

## 5. Disaster Recovery & Operational Runbooks

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

