# Forge: AWS Architecture & Deployment Guide (Reference & Cost-Optimized Options)

Forge provides two complementary AWS deployment blueprints tailored to different operational requirements and budget constraints:

1. **Track A: Cost-Optimized Cloud Deployment ($15–$20 / month)**: A lean, single-instance production topology on AWS EC2 (`t4g.small` ARM Graviton or `t3.small` x86) running Docker Compose, Caddy (automatic Let's Encrypt TLS via `sslip.io` or custom domain), containerized PostgreSQL 16, external MongoDB Atlas M0 (Free 3-node replica set), and native AWS SQS & S3 via an EC2 IAM Instance Profile. **Recommended for hosting live portfolio demonstrations sustainably for 6–10 months within an introductory $100–$200 cloud credit grant.**
2. **Track B: Enterprise Reference Architecture (Not Deployed)**: A fully distributed, multi-AZ enterprise infrastructure declared in [infra/terraform/](file:///d:/MyStorage/PROJECTS/Custom_Objects_Workflow_Studio/infra/terraform) using AWS ECS Fargate, Multi-AZ RDS PostgreSQL 16, Application Load Balancer with unauthenticated `/healthz/` probe, NAT Gateway, CloudFront CDN with Origin Access Control (OAC), SQS Dead Letter Queue (DLQ), and encrypted S3 storage.

---

## 1. Cloud Architecture Overviews

### Track A: Cost-Optimized Cloud Topology ($15–$20 / month)

```
                            Internet / Browser Clients
                                        │
                                        ▼ (HTTPS :443 / HTTP :80)
┌───────────────────────────────────────┴───────────────────────────────────────┐
│ AWS EC2 Instance (t4g.small / t3.small, 2 vCPU, 2 GB RAM, Elastic IP)          │
│ Security Group: Ingress 80/443 (0.0.0.0/0), Ingress 22 (Admin IP only)        │
│ IMDSv2 Enforced: http-tokens=required, http-put-response-hop-limit=1          │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────────┐  │
│  │ Caddy 2.8 Reverse Proxy (Auto-TLS via Let's Encrypt / sslip.io)         │  │
│  │  • Serves static React 19 Web Studio SPA build (/srv/web/dist)          │  │
│  │  • Proxies /api/* and /healthz/* to Gunicorn API container on :8000     │  │
│  └────────────────────────────────────┬────────────────────────────────────┘  │
│                                       │ (Bridge Network: forge-internal)      │
│          ┌────────────────────────────┼────────────────────────────┐          │
│          ▼                            ▼                            ▼          │
│   Forge Control Plane API      PostgreSQL 16 Engine         Forge Workers     │
│   • Django 5.1 / Gunicorn 22   • postgres:16-alpine         • OutboxRelayWorker│
│   • Sliding-window throttle    • auth_tenants, users, keys  • Webhook Dispatch │
│   • Liveness: GET /healthz/    • Persistent EBS Volume      • CSV S3 Exporter  │
│   • Port 8000 (Internal only)  • Port 5432 (Internal only)  • Leased Ledger    │
│          │                                                         │          │
└──────────┼─────────────────────────────────────────────────────────┼──────────┘
           │ (Multi-Document ACID Transactions)                      │ (IAM Role)
           ▼                                                         ▼
MongoDB Atlas on AWS (rs0 Free Tier M0)                    AWS Managed Cloud Services
 • 3-Node WiredTiger Replica Set (us-east-1)               • AWS Standard SQS Queue & DLQ
 • collections: records, unique_keys, outbox, ledger       • AWS S3 Bucket (CSV Exports)
 • Whitelisted Elastic IP + SCRAM Auth                     • AWS SSM Parameter Store (Secrets)
```

---

### Track B: Enterprise Reference Architecture (Not Currently Deployed)

```
                                 Internet / Clients
                                         │
                                         ▼ (Route 53 DNS + TLS HTTPS)
                       AWS CloudFront CDN (Global Edge Entry Point)
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 ▼ (Static S3 Origin: /*)                         ▼ (Dynamic API Origin: /api/*)
    S3 Bucket (forge-prod-studio-web)              Application Load Balancer (ALB)
    • React 19 Web Studio (SPA Dist)               • Public Subnets (AZ-a, AZ-b)
    • CloudFront Origin Access Control (OAC)       • Liveness Probe: GET /healthz/ (200 OK)
    • Private bucket access block                  • Forwarding Port 8000 to Target Group
                                                                 │
                                                                 ▼ (Port 8000)
                                                    ECS Fargate: API Tasks (2+ Tasks)
                                                    • Django 5.1 / Gunicorn 22
                                                    • TenantRateThrottle (sliding-window)
                                                    • Private Subnets (No public IP)
                                                                 │
                               ┌─────────────────────────────────┼─────────────────────────────────┐
                               ▼ (Multi-Doc Transactions)        ▼ (Relational Metadata)           ▼ (Atomic Thin Outbox)
                    MongoDB Atlas on AWS (rs0)             AWS RDS PostgreSQL 16                 MongoDB outbox Collection
                    • 3-node WiredTiger Replica Set        • Multi-AZ Private Subnet             • Claim: {$or: [pending, expired]}
                    • collections: records, unique_keys    • auth_tenants, users, api_keys       • Lease duration: 30s
                               ▲                                                                   │
                               │                                                                   ▼
                               │                                                       ECS Fargate: Worker Tasks (1+ Task)
                               │                                                         • OutboxRelayWorker
                               │                                                         • Private Subnet via NAT Gateway
                               │                                                                   │
                               │                                                                   ▼ (event.version)
                               │                                                          AWS Standard SQS Queues
                               │                                                           • forge-prod-events-queue
                               │                                                           • forge-prod-events-dlq (Dead Letter)
                               │                                                                   │
                               ├───────────────────────────────────────────────────────────────────┤
                               ▼                                                                   ▼
               SSRF-Hardened Webhook Dispatcher                                     CSV Streaming S3 Exporter
               • Anti-SSRF DNS Resolution & IP Pinning                               • Low-memory chunk streaming
               • ipaddress.is_global validation                                      • Deterministic S3 Key:
               • TLS SNI & Host preservation                                           tenants/{id}/exports/{export_id}.csv
               • HMAC-SHA256 timestamped signatures                                  • Role-masked CSV rows (FieldPermissionManager)
               • Leased deduplication ledger (processed_events)                       • Private encrypted S3 Bucket
```

---

## 2. Realistic 2026 Bill of Materials (BOM) & Cost Comparison

AWS pricing changes in recent years—notably the introduction of public IPv4 charges ($0.005/hr per IP as of February 2024), NAT Gateway hourly minimums, and ALB base hourly fees—make distributed enterprise stacks significantly more expensive to idle than developers anticipate.

The table below contrasts the real-world monthly operating costs for both deployment tracks in `us-east-1`:

| Infrastructure Component | Track A: Cost-Optimized (Single EC2 + Atlas M0) | Track B: Enterprise Reference (ECS Fargate + Multi-AZ RDS) | Pricing Basis & Notes (2025/2026 AWS Rates) |
| :--- | :--- | :--- | :--- |
| **Compute (API & Workers)** | **~$12.26 / mo** (`t4g.small` ARM, 2 vCPU, 2 GB) or ~$15.18 (`t3.small`) | **~$54.00 / mo** (3 tasks × 0.5 vCPU, 1 GB RAM x86 Fargate) | Fargate x86: $0.04048/vCPU-hr + $0.004445/GB-hr = ~$18/task/mo. Single EC2 runs API + Workers + Caddy in containers. |
| **Relational Database** | **$0.00 / mo** (Containerized PostgreSQL 16 on local EBS) | **~$29.30 / mo** (Multi-AZ RDS `db.t4g.micro`, 20 GB gp3) | Multi-AZ doubles instance hours ($13.50 × 2 = ~$27.00) + $2.30 storage. Containerized Postgres uses host RAM. |
| **Document Database** | **$0.00 / mo** (MongoDB Atlas M0 Free Tier, 3-node rs0) | **$0.00 / mo** (Atlas M0 Free Tier) or **~$57.00 / mo** (M10 Dedicated) | Multi-document transactions require a replica set (`rs0`), provided free on Atlas M0 (512 MB, 100 ops/s). |
| **Load Balancer / Ingress** | **$0.00 / mo** (Caddy reverse proxy with free Let's Encrypt TLS) | **~$18.50 / mo** (AWS Application Load Balancer) | ALB base fee: $0.0225/hr = $16.43/mo + ~$2.00 LCU traffic minimums. Caddy runs inside the EC2 instance for free. |
| **Networking & NAT** | **$0.00 / mo** (Direct Internet Egress via Internet Gateway) | **~$35.00 / mo** (1 AWS NAT Gateway in public subnet) | NAT Gateway base fee: $0.045/hr = $32.85/mo + $0.045/GB data processed. Required for private Fargate outbound traffic. |
| **Public IPv4 Addresses** | **~$3.65 / mo** (1 Elastic IP assigned to EC2) | **~$10.95 / mo** (3 Public IPs: 1 NAT Gateway IP + 2 ALB IPs) | AWS bills $0.005/hr ($3.65/mo) per in-use or idle public IPv4 address across all AWS resources since Feb 2024. |
| **Storage (EBS / S3)** | **~$1.60 / mo** (20 GB gp3 root EBS volume @ $0.08/GB) | **~$1.00 / mo** (S3 CSV Exports bucket + CloudFront CDN) | S3 standard storage ($0.023/GB) and CloudFront bandwidth (first 1 TB free). |
| **Messaging (SQS + DLQ)** | **< $0.10 / mo** (Standard SQS Pay-Per-Request) | **< $0.50 / mo** (Standard SQS Pay-Per-Request) | AWS SQS provides 1,000,000 requests per month free; pay-as-you-go thereafter ($0.40 per 1M requests). |
| **Secrets & Config** | **$0.00 / mo** (AWS SSM Parameter Store Standard Tier) | **$0.00 / mo** (AWS SSM Parameter Store Standard Tier) | Standard parameters are free of charge. |
| **TOTAL ESTIMATED COST** | **~$17.50 / month** | **~$148.75 / month** (Atlas M0) / **~$205.75 / month** (Atlas M10) | **Introductory credits ($100–$200) last 6–11 months on Track A vs. only 30–45 days on Track B.** |

> [!WARNING]
> **Why Track B Depletes Cloud Credits Rapidly**:
> Even with zero incoming web traffic, the Enterprise Reference Architecture incurs fixed hourly charges for the NAT Gateway ($32.85), Application Load Balancer ($16.43), three public IPv4 addresses ($10.95), and Multi-AZ RDS ($27.00). This totals over **$87/month in fixed baseline idle cost**, burning through a $100 AWS starter credit in roughly 35 days.

---

## 3. Track A: Cost-Optimized Cloud Deployment Walkthrough ($15–$20 / mo)

This deployment model runs all Forge components on a single hardened AWS EC2 instance using production Docker Compose and Caddy with automatic Let's Encrypt TLS.

### Step 1: Launch EC2 Instance & Configure Security Group

1. Launch an EC2 instance in `us-east-1`:
   - **AMI**: Amazon Linux 2023 (ARM64) or Ubuntu 24.04 LTS (ARM64)
   - **Instance Type**: `t4g.small` (2 vCPU, 2 GB RAM, ~$12.26/mo) or `t3.small` (x86, 2 vCPU, 2 GB RAM, ~$15.18/mo)
   - **Storage**: 20 GB `gp3` root volume (3,000 IOPS, 125 MB/s)
   - **Public IP**: Allocate and associate 1 **Elastic IP** (e.g. `54.210.12.34`)

2. Configure Security Group (`forge-single-host-sg`):
   - **Inbound Port 80 (HTTP)**: `0.0.0.0/0` (for Let's Encrypt ACME HTTP-01 challenges and HTTPS redirection)
   - **Inbound Port 443 (HTTPS)**: `0.0.0.0/0` (for production TLS traffic)
   - **Inbound Port 22 (SSH)**: `<YOUR_ADMIN_IP>/32` (strictly restricted to your IP address)
   - **Outbound**: `0.0.0.0/0` (for outbound SQS, S3, MongoDB Atlas, and webhook delivery)

### Step 2: Enforce IMDSv2 (Anti-SSRF Protection)

To prevent container breakout SSRF attacks from stealing EC2 instance metadata credentials, enforce IMDSv2 with a metadata token hop limit of 1:

```bash
aws ec2 modify-instance-metadata-options \
  --instance-id <INSTANCE_ID> \
  --http-tokens required \
  --http-put-response-hop-limit 1 \
  --http-endpoint enabled
```
*Why this matters*: A hop limit of 1 ensures the metadata service response cannot travel across Docker bridge network hops (`forge-internal`), blocking any SSRF or container breakout from accessing host instance credentials.

### Step 3: Attach Least-Privilege IAM Instance Profile

Create an IAM role `ForgeEC2InstanceRole` and attach it to the EC2 instance:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowSQSAccess",
      "Effect": "Allow",
      "Action": [
        "sqs:SendMessage",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes"
      ],
      "Resource": "arn:aws:sqs:us-east-1:*:forge-events-*"
    },
    {
      "Sid": "AllowS3ExportAccess",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::forge-csv-exports-*/*"
    },
    {
      "Sid": "AllowSSMParameterRead",
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameter",
        "ssm:GetParameters"
      ],
      "Resource": "arn:aws:ssm:us-east-1:*:parameter/forge/prod/*"
    }
  ]
}
```

### Step 4: Provision MongoDB Atlas M0 Replica Set

1. Sign up or log in at [MongoDB Atlas](https://www.mongodb.com/cloud/atlas).
2. Create an **M0 Free Cluster** in AWS `us-east-1`. Atlas automatically provisions a 3-node WiredTiger replica set (`rs0`) with multi-document ACID transactions enabled.
3. Under **Database Access**: Create database user `forge_app_user` with password authentication.
4. Under **Network Access**: Add the **Elastic IP** of your EC2 instance (`54.210.12.34/32`).
5. Copy your standard connection string:
   ```
   mongodb+srv://forge_app_user:<PASSWORD>@<CLUSTER>.mongodb.net/?retryWrites=true&w=majority
   ```

### Step 5: Install Docker & Compose on the Host

Connect to your EC2 instance via SSH:
```bash
ssh -i your-key.pem ec2-user@54.210.12.34

# On Amazon Linux 2023:
sudo dnf update -y
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user

# Install Docker Compose plugin v2:
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL https://github.com/docker/compose/releases/download/v2.29.2/docker-compose-linux-aarch64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# Re-login for group permissions:
exit
ssh -i your-key.pem ec2-user@54.210.12.34
docker compose version
```

### Step 6: Clone Repository & Build Static Studio Frontend

```bash
git clone https://github.com/SambitAmanta/Custom-Objects-Workflow_Studio.git /srv/forge
cd /srv/forge

# Install Node.js 20 to build Web Studio React distribution:
curl -fsSL https://rpm.nodesource.com/setup_20.x | sudo bash -
sudo dnf install -y nodejs
npm ci
npm run build
# Built files land in apps/web/dist
```

### Step 7: Configure Environment & Launch Docker Compose

Create the production environment file `/srv/forge/.env`:

```bash
cat << 'EOF' > /srv/forge/.env
POSTGRES_DB=forge_prod
POSTGRES_USER=forge_user
POSTGRES_PASSWORD=$(openssl rand -hex 16)

DJANGO_SECRET_KEY=$(openssl rand -base64 48)
FORGE_SERVER_SECRET=$(openssl rand -hex 32)
JWT_SECRET_KEY=$(openssl rand -base64 36)

# MongoDB Atlas connection string
MONGO_URI=mongodb+srv://forge_app_user:<PASSWORD>@<CLUSTER>.mongodb.net/?retryWrites=true&w=majority

# AWS Region & SQS / S3 names
AWS_REGION=us-east-1
SQS_OUTBOX_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/<ACCOUNT_ID>/forge-events-queue
SQS_DLQ_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/<ACCOUNT_ID>/forge-events-dlq
S3_EXPORT_BUCKET_NAME=forge-csv-exports-<ACCOUNT_ID>

# Domain configuration: use <ip>.sslip.io or your custom registered domain
# e.g., 54-210-12-34.sslip.io resolves automatically to 54.210.12.34
DOMAIN_NAME=54-210-12-34.sslip.io
ACME_EMAIL=admin@example.com
EOF
```

Launch the stack using [infra/docker/docker-compose.prod.yml](file:///d:/MyStorage/PROJECTS/Custom_Objects_Workflow_Studio/infra/docker/docker-compose.prod.yml):

```bash
docker compose -f infra/docker/docker-compose.prod.yml up -d --build
```

### Step 8: Apply Migrations & Seed Demo Data

```bash
# Run database migrations and bootstrap initial tenant/users:
docker compose -f infra/docker/docker-compose.prod.yml exec api python scripts/seed_demo.py
```

### Step 9: Verify Live Deployment

1. **Test Liveness Health Check (No Auth Required)**:
   ```bash
   curl -i https://54-210-12-34.sslip.io/healthz/
   # Returns: HTTP 200 OK {"status": "ok", "service": "forge-api"}
   ```

2. **Test Readiness Probe (Checks Relational DB & MongoDB Connectivity)**:
   ```bash
   curl -i https://54-210-12-34.sslip.io/readyz/
   # Returns: HTTP 200 OK {"status": "ready", "checks": {"database": "connected", "mongodb": "connected"}}
   ```

3. **Verify Authentication Gate**:
   ```bash
   curl -i https://54-210-12-34.sslip.io/api/v1/schemas/types/
   # Returns: HTTP 401 Unauthorized (confirming JWT guard is active)
   ```

4. **Access Web Studio in Browser**:
   Open `https://54-210-12-34.sslip.io` in Chrome or Firefox. Caddy will have automatically obtained a trusted Let's Encrypt TLS certificate for your `sslip.io` domain with a green lock indicator.
   - Log in as `alice@acme.com` / `password123`.

---

## 4. Track B: Enterprise Reference Architecture (Terraform + Fargate)

For production enterprise workloads requiring multi-region failover, automated autoscaling, and dedicated private subnets, Forge provides full Terraform infrastructure declarations in [infra/terraform/](file:///d:/MyStorage/PROJECTS/Custom_Objects_Workflow_Studio/infra/terraform).

### Summary of Terraform Infrastructure Modules:
- `vpc.tf`: Custom VPC (`10.0.0.0/16`), 2 public subnets, 2 private subnets, Internet Gateway, and NAT Gateway.
- `security_groups.tf`: Least-privilege ingress/egress boundaries between ALB, ECS API, ECS Workers, and RDS.
- `alb.tf`: Application Load Balancer with health checks configured on **`/healthz/`** (port 8000, matcher `200`).
- `rds.tf`: AWS RDS PostgreSQL 16 instance (`db.t4g.micro`) with automated password storage in AWS SSM.
- `main.tf`: AWS Standard SQS Queue (`forge-prod-events-queue`), DLQ, and private AES-256 encrypted S3 exports bucket.
- `ecs.tf`: ECS Fargate cluster, task definitions for API (2 tasks) and Workers (1 task), CloudWatch log groups, and IAM roles.
- `frontend.tf`: S3 bucket hosting React SPA with CloudFront Origin Access Control (OAC), multi-origin routing (S3 static files + ALB `/api/*`), and SPA 404 rewrite handling.

### Provisioning Commands:

```bash
cd infra/terraform

# 1. Initialize Terraform providers (AWS ~> 5.0)
terraform init

# 2. Review infrastructure plan
terraform plan -out=tfplan \
  -var="environment=prod" \
  -var="aws_region=us-east-1" \
  -var="app_name=forge"

# 3. Apply infrastructure
terraform apply tfplan
```

### Outputs Captured from Terraform:
- `alb_dns_name`: `forge-prod-alb-123456789.us-east-1.elb.amazonaws.com`
- `cloudfront_domain_name`: `d123456abcdef8.cloudfront.net`
- `events_queue_url`: `https://sqs.us-east-1.amazonaws.com/<ACCOUNT_ID>/forge-prod-events-queue`
- `exports_bucket_name`: `forge-prod-csv-exports-us-east-1`
- `rds_endpoint`: `forge-prod-pg.c123456789.us-east-1.rds.amazonaws.com:5432`

---

## 5. Disaster Recovery & Operational Runbooks

### SQS Dead Letter Queue (DLQ) Redrive Runbook
If an external webhook target experiences prolonged downtime or responds with repeated 5xx errors, messages route automatically to the DLQ after 5 attempts:

```bash
# 1. Check current message depth in Dead Letter Queue
aws sqs get-queue-attributes \
  --queue-url $(terraform output -raw events_dlq_url) \
  --attribute-names ApproximateNumberOfMessages

# 2. Once the target endpoint is restored, redrive messages back to the primary queue
aws sqs start-message-move-task \
  --source-arn $(terraform output -raw events_dlq_arn) \
  --destination-arn $(terraform output -raw events_queue_arn)
```

### Zero-Downtime Rolling Container Updates

#### On Track A (Docker Compose):
```bash
cd /srv/forge
git pull origin main
npm ci && npm run build
docker compose -f infra/docker/docker-compose.prod.yml build
docker compose -f infra/docker/docker-compose.prod.yml up -d --no-deps api workers
```

#### On Track B (ECS Fargate):
```bash
aws ecs update-service \
  --cluster forge-prod-cluster \
  --service forge-prod-api-service \
  --force-new-deployment
```
ECS deploys new tasks in private subnets, validates the unauthenticated `/healthz/` probe, registers them with the ALB target group, and drains old tasks with zero dropped HTTP requests.
