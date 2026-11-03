# ECS task definitions

Reference copies of the Fargate task definitions that `infra/terraform` registers
(placeholders `<ACCOUNT_ID>`, `<REGION>`, `<ELASTICACHE_PRIMARY_ENDPOINT>` and the
secret ARN are filled in by terraform). The deploy workflow does **not** read
these files; it pulls the live definition with `aws ecs describe-task-definition`
so that environment and secrets always match what terraform manages, and only
swaps the container image. Keep these in sync when you change `main.tf`, or
use them to register a task definition by hand:

```bash
aws ecs register-task-definition --cli-input-json file://task-definition-api.json
```
