# AWS deployment design

This is a deployment design, not a claim that the development application is ready for real accounts. No AWS resources are created by the local application.

## Network and identity

Route 53 resolves the public application hostname to CloudFront. AWS WAF applies managed rules, request-size limits and rate policies. CloudFront forwards only application paths to an HTTPS Application Load Balancer. Restrict the origin with a CloudFront origin secret and security controls. API Gateway is an alternative for a separate API product; do not expose both without a deliberate trust design.

Use Cognito authorization-code flow with PKCE and validate issuer, audience and JWKS signatures in the backend. Replace the demo password grant. Use an independent audited step-up provider; a Cognito login alone does not authorize a banking write. Bind step-up to the exact action, customer, session, amount/resource and expiry.

Place ECS Fargate tasks (or EKS workloads) in private application subnets across at least two availability zones. Only the ALB uses public subnets. MCP tasks, RDS PostgreSQL and ElastiCache Redis receive no public IPs and have no internet-facing listeners. Use service discovery, per-service security groups, IAM task roles and mTLS/service identity for internal calls. The API may contact MCP ports; MCPs may contact only the banking API. Only the API can contact RDS and Redis.

## Managed services

| Service | Role and controls |
|---|---|
| ECR | Scan and sign immutable application images; deploy by digest |
| ECS / EKS | Non-root tasks, read-only filesystems where supported, resource limits and autoscaling |
| Amazon Bedrock | Optional approved model through private endpoints; use least-privilege task roles |
| RDS PostgreSQL | Multi-AZ, private subnets, TLS, encrypted volumes, limited database roles |
| ElastiCache Redis | Private TLS/auth endpoints, TTLs and no public ingress |
| S3 | Private statement objects if replacing in-memory CSVs; short expiry and authorization before signing |
| KMS | Encryption keys for RDS, Redis, S3, backups and session-token envelope encryption |
| Secrets Manager | Database/model credentials and signing material with rotation; no secrets in task definitions |
| CloudWatch | Metadata-only logs, alarms, dashboards and retention controls |
| OpenTelemetry | ADOT collectors export scrubbed spans; never export prompt text or credentials |
| AWS Backup | Policy-based RDS/S3 backup, cross-account copies and restoration drills |

Use VPC endpoints for Secrets Manager, ECR, S3, CloudWatch and Bedrock where supported. Keep outbound provider access disabled unless explicitly approved. Separate the checkpoint database role from the banking ledger role. Grant migration privileges only to the deployment job.

## Release and recovery

Run security/evaluation gates and dependency/secret scans before image publication. Apply migrations through a single job, take a recoverable backup, deploy a canary, run synthetic acceptance checks and observe error/latency/authorization metrics. Roll back application images using immutable digests; database rollback requires a reviewed compatibility plan.

Test backup restoration, signing-key rotation, Redis loss, MCP failures, OTP delivery failures, concurrent submissions and checkpoint recovery. The current repository provides the application and local Compose stack; environment-specific infrastructure provisioning, OIDC and real OTP integration remain prerequisites for production.
