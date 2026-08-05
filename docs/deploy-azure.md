# Deploying the API to Azure Container Apps

The frontend is a static bundle on GitHub Pages (see
`.github/workflows/deploy-pages.yml`). This covers the API, which runs as a
container and is deployed by `.github/workflows/deploy-api.yml`.

Run these once to provision. Afterwards every push to `main` that touches the
backend deploys itself.

## Before you start

- The API **must** live on a subdomain of the site's domain —
  `api.shortimer.com`. The session cookie is `SameSite=Lax`, so a browser on
  `shortimer.com` will not send it to an API on any other registrable domain,
  and every authenticated request would 401.
- Images publish to GHCR, which is free for this public repo. There's no
  Azure Container Registry to create or pay for.

```bash
# Adjust to taste; the rest of the guide uses these.
export RG=shortimer-rg
export LOCATION=eastus
export ENVIRONMENT=shortimer-env
export APP=shortimer-api
export API_HOST=api.shortimer.com
export IMAGE=ghcr.io/devdupont/short-timer/api:latest

az login
az account set --subscription "<your-subscription-id>"
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.OperationalInsights --wait
```

## 1. Resource group and environment

```bash
az group create --name "$RG" --location "$LOCATION"

az containerapp env create \
  --name "$ENVIRONMENT" \
  --resource-group "$RG" \
  --location "$LOCATION"
```

## 2. Create the app

`--min-replicas 0` is what keeps this inside the free grant — see
[Cost](#cost) before changing it.

```bash
az containerapp create \
  --name "$APP" \
  --resource-group "$RG" \
  --environment "$ENVIRONMENT" \
  --image "$IMAGE" \
  --target-port 8000 \
  --ingress external \
  --min-replicas 0 \
  --max-replicas 3 \
  --cpu 0.25 --memory 0.5Gi
```

## 3. Secrets and configuration

Secrets are stored by Container Apps and surfaced as environment variables, so
they never appear in the image or in the workflow.

```bash
az containerapp secret set \
  --name "$APP" --resource-group "$RG" \
  --secrets \
    mongodb-uri="mongodb+srv://<user>:<password>@<cluster>/?retryWrites=true&w=majority" \
    anthropic-api-key="<your key>" \
    app-passcode="<the shared passcode>"

az containerapp update \
  --name "$APP" --resource-group "$RG" \
  --set-env-vars \
    MONGODB_URI=secretref:mongodb-uri \
    ANTHROPIC_API_KEY=secretref:anthropic-api-key \
    APP_PASSCODE=secretref:app-passcode \
    MONGODB_DB_NAME=short_timer \
    SESSION_COOKIE_SECURE=true \
    CORS_ORIGINS=https://shortimer.com \
    TRUSTED_PROXY_HOPS=1
```

`TRUSTED_PROXY_HOPS=1` is load-bearing. Container Apps' ingress *appends* to
`X-Forwarded-For`, so anything the caller sends arrives to the left of the
address the ingress observed. The app counts in from the right by this many
hops; leaving it at `0` would fall back to the ingress's own address and put
every visitor in one rate-limit bucket, while trusting the leftmost entry
would let a caller forge a fresh bucket per request and walk past the login
limit.

## 4. Health probes

`/api/health` is liveness (the process is up). `/api/ready` also pings Mongo,
so a replica that can't reach Atlas stops receiving traffic instead of serving
errors.

`--yaml` takes a file path rather than stdin, so write one first:

```bash
cat > /tmp/probes.yaml <<'YAML'
properties:
  template:
    containers:
      - name: shortimer-api
        image: ghcr.io/devdupont/short-timer/api:latest
        probes:
          - type: Liveness
            httpGet: { path: /api/health, port: 8000 }
            initialDelaySeconds: 10
            periodSeconds: 30
          - type: Readiness
            httpGet: { path: /api/ready, port: 8000 }
            initialDelaySeconds: 5
            periodSeconds: 15
YAML

az containerapp update --name "$APP" --resource-group "$RG" --yaml /tmp/probes.yaml
```

## 5. Custom domain

Get the values to point DNS at, then add a CNAME and a TXT record at your
registrar:

```bash
az containerapp show --name "$APP" --resource-group "$RG" \
  --query "properties.configuration.ingress.fqdn" -o tsv
az containerapp show --name "$APP" --resource-group "$RG" \
  --query "properties.customDomainVerificationId" -o tsv
```

| Type  | Name                 | Value                          |
| ----- | -------------------- | ------------------------------ |
| CNAME | `api`                | the FQDN from above            |
| TXT   | `asuid.api`          | the verification ID from above |

Then bind the hostname and let Azure issue a free managed certificate:

```bash
az containerapp hostname add \
  --hostname "$API_HOST" --name "$APP" --resource-group "$RG"

az containerapp hostname bind \
  --hostname "$API_HOST" --name "$APP" --resource-group "$RG" \
  --environment "$ENVIRONMENT" --validation-method CNAME
```

## 6. MongoDB Atlas access

Container Apps egresses from a pool of addresses that changes, so pinning an
allowlist isn't practical without a NAT gateway (which costs money and isn't
worth it here). For the demo, allow `0.0.0.0/0` in Atlas **Network Access** and
rely on a strong database password. Revisit this when the app moves to
permanent hosting.

## 7. Let GitHub Actions deploy without a stored password

Federated credentials let the workflow authenticate with a short-lived OIDC
token, so there's no client secret to rotate or leak.

```bash
export SUBSCRIPTION_ID=$(az account show --query id -o tsv)

az ad app create --display-name shortimer-deploy
export APP_ID=$(az ad app list --display-name shortimer-deploy --query "[0].appId" -o tsv)
az ad sp create --id "$APP_ID"

# Scope the role to just this resource group.
az role assignment create \
  --assignee "$APP_ID" \
  --role Contributor \
  --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RG"

# Trust pushes to main from this repo only.
az ad app federated-credential create --id "$APP_ID" --parameters '{
  "name": "github-main",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:devdupont/short-timer:ref:refs/heads/main",
  "audiences": ["api://AzureADTokenExchange"]
}'
```

The deploy job deliberately does **not** use a GitHub environment. Adding one
changes the OIDC subject Azure federates on, and the mismatch surfaces as an
opaque login failure. If you later want an approval gate, add
`environment: production` to the job *and* this second credential together:

```bash
az ad app federated-credential create --id "$APP_ID" --parameters '{
  "name": "github-production-env",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:devdupont/short-timer:environment:production",
  "audiences": ["api://AzureADTokenExchange"]
}'
```

## 8. GitHub settings the workflows expect

Repository **secrets**:

| Name                    | Value                                      |
| ----------------------- | ------------------------------------------ |
| `AZURE_CLIENT_ID`       | `$APP_ID` from above                       |
| `AZURE_TENANT_ID`       | `az account show --query tenantId -o tsv`   |
| `AZURE_SUBSCRIPTION_ID` | `az account show --query id -o tsv`         |

Repository **variables**:

| Name                    | Value                        |
| ----------------------- | ---------------------------- |
| `AZURE_RESOURCE_GROUP`  | `shortimer-rg`               |
| `AZURE_CONTAINERAPP`    | `shortimer-api`              |
| `API_HOSTNAME`          | `api.shortimer.com`          |
| `VITE_API_BASE_URL`     | `https://api.shortimer.com`  |

`VITE_API_BASE_URL` belongs to the Pages workflow. Vite inlines it at build
time, so it's public by nature — a variable, not a secret. Until it's set the
frontend falls back to `localhost` and every API call fails.

## Cost

The Container Apps free grant is 180,000 vCPU-seconds and 360,000 GiB-seconds
per month. At the 0.25 vCPU minimum, running one replica continuously would
cost roughly 657,000 vCPU-seconds — about 3.6x the grant.

So `--min-replicas 0` is what keeps this free. The trade is a cold start of a
few seconds on the first request after an idle period, which is worth thinking
about for a wall-mounted gym display. `--min-replicas 1` removes it and costs
roughly $5–10/month.

Scaling to zero doesn't break the background work: the daily WOD refresh and
the monthly parse-pool sweep both run at startup and are guarded against
running too often, so they fire on the next visit rather than on a timer.

## Verifying a deploy

The workflow already polls `/api/ready` and fails if a new revision never
becomes ready. By hand:

```bash
curl -s https://api.shortimer.com/api/ready          # {"status":"ok","database":"ok"}
az containerapp revision list --name "$APP" --resource-group "$RG" -o table
az containerapp logs show --name "$APP" --resource-group "$RG" --follow
```
