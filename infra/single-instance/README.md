# Forge: Cost-Optimized Single-Instance AWS Deployment ($15–$20/mo)

This directory provides the Docker Compose and Caddy configuration for running Forge on a single AWS EC2 instance (`t4g.small` or `t3.small`) connected to MongoDB Atlas M0 (Free Tier) and real AWS SQS and S3 services via an attached EC2 IAM Role.

---

## Architecture Highlights
- **EC2 Instance**: `t4g.small` (ARM64, 2 vCPU, 2 GB RAM, ~$12.26/mo) + 1 Public IPv4 ($3.65/mo) + 20 GB gp3 root EBS ($1.60/mo) = **~$17.50 / month**.
- **MongoDB Atlas**: M0 Sandbox (Free Tier, 3-node WiredTiger replica set `rs0`, 512 MB, 100 ops/s cap).
- **AWS SQS & S3**: Real AWS managed queues and buckets authenticated via EC2 IAM Instance Profile (zero static secrets on disk).
- **Caddy Reverse Proxy**: Handles automatic HTTPS with Let's Encrypt certificates using `sslip.io` (e.g. `forge.<PUBLIC_IP>.sslip.io`) without purchasing a domain name, and serves the pre-built React 19 Web Studio directly from local disk.

---

## 1. AWS IAM Instance Profile Policy

Attach an IAM Role to your EC2 instance with the following inline policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ForgeSQSAccess",
      "Effect": "Allow",
      "Action": [
        "sqs:SendMessage",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes"
      ],
      "Resource": "arn:aws:sqs:*:*:forge-prod-events-*"
    },
    {
      "Sid": "ForgeS3Access",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::forge-prod-csv-exports*/*"
    }
  ]
}
```

---

## 2. Hardening: IMDSv2 Enforcement

Enforce IMDSv2 with a hop limit of 1 to ensure containerized processes and SSRF vectors cannot harvest host IAM credentials from `169.254.169.254`:

```bash
aws ec2 modify-instance-metadata-options \
  --instance-id <INSTANCE_ID> \
  --http-tokens required \
  --http-put-response-hop-limit 1
```

*(Note: Forge's internal SSRF blocklist in `apps/workers/consumers.py` also explicitly rejects link-local and private IPs via `ipaddress.is_global`.)*

---

## 3. Deployment Steps on the EC2 Host

```bash
# 1. Clone repository
git clone https://github.com/SambitAmanta/Custom-Objects-Workflow-Studio.git
cd Custom-Objects-Workflow-Studio

# 2. Build React Web Studio production bundle
npm install
npm run build

# 3. Create .env file for the single-instance stack
cat << 'EOF' > infra/single-instance/.env
DOMAIN=forge.<YOUR_PUBLIC_IP>.sslip.io
POSTGRES_DB=forge_db
POSTGRES_USER=postgres
POSTGRES_PASSWORD=generate_a_secure_postgres_password
DJANGO_SECRET_KEY=generate_a_django_secret_key
FORGE_SERVER_SECRET=generate_64_hex_chars_for_uniqueness_hmac
JWT_SECRET_KEY=generate_a_jwt_signing_secret
MONGO_URI=mongodb+srv://forge_user:<PASSWORD>@<ATLAS_HOST>/?retryWrites=true&w=majority
AWS_DEFAULT_REGION=us-east-1
AWS_REGION=us-east-1
SQS_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/<ACCOUNT_ID>/forge-prod-events-queue
S3_EXPORT_BUCKET=forge-prod-csv-exports-<ACCOUNT_ID>
EOF

# 4. Launch with Docker Compose
cd infra/single-instance
docker compose -f docker-compose.prod-single.yml up -d --build

# 5. Run database migrations and demo seeds
docker compose -f docker-compose.prod-single.yml exec api python scripts/seed_demo.py
```
