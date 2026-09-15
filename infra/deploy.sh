#!/usr/bin/env bash
# Digital Employee - Azure provisioning. Idempotent. Plain az cli so IT can read what it installs.
#
#   ./infra/deploy.sh infra   # resource group, identity, roles, Key Vault, Postgres, Container Apps env, model deployment
#   ./infra/deploy.sh app     # build image in ACR and create/update the Container App (SKIP_BUILD=1 to reuse the image for TAG)
#   ./infra/deploy.sh         # both
#
# Every name below is a variable so a second firm or region is a config change.
set -euo pipefail

NAME=${NAME:-emp}
RG=${RG:-zybit-${NAME}-rg}
LOC=${LOC:-centralus}
ACR=${ACR:-zybitacrcore}                 # shared registry
ACR_RG=${ACR_RG:-zybit-shared-rg}
FOUNDRY=${FOUNDRY:-zybit-project-resource}   # Microsoft Foundry resource that serves the model
FOUNDRY_RG=${FOUNDRY_RG:-zybit-rg}
MODEL=${MODEL:-gpt-5.6-sol}
MODEL_VERSION=${MODEL_VERSION:-2026-07-09}
MODEL_CAPACITY=${MODEL_CAPACITY:-50}

PG=${NAME}-pg; PG_DB=employee; PG_USER=emp
KV=zybit-${NAME}-kv
ID=${NAME}-id
LOGS=${NAME}-logs
ENV=${NAME}-env
APP=${NAME}-app
TAG=${TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo dev)}
IMAGE=${ACR}.azurecr.io/digital-employee:${TAG}

say() { printf '\n== %s\n' "$*"; }

assign_role() { # principal-object-id principal-type role scope
  az role assignment create --assignee-object-id "$1" --assignee-principal-type "$2" --role "$3" --scope "$4" -o none 2>/dev/null \
    || echo "   role '$3' already assigned"
}

infra() {
  say "resource group $RG ($LOC)"
  az group create -n "$RG" -l "$LOC" -o none

  say "managed identity $ID"
  az identity create -n "$ID" -g "$RG" -l "$LOC" -o none
  ID_PRINCIPAL=$(az identity show -n "$ID" -g "$RG" --query principalId -o tsv)
  ID_RES=$(az identity show -n "$ID" -g "$RG" --query id -o tsv)
  ME=$(az ad signed-in-user show --query id -o tsv)

  say "roles: pull images, call the model"
  assign_role "$ID_PRINCIPAL" ServicePrincipal AcrPull "$(az acr show -n "$ACR" -g "$ACR_RG" --query id -o tsv)"
  FOUNDRY_ID=$(az cognitiveservices account show -n "$FOUNDRY" -g "$FOUNDRY_RG" --query id -o tsv)
  assign_role "$ID_PRINCIPAL" ServicePrincipal "Cognitive Services OpenAI User" "$FOUNDRY_ID"
  assign_role "$ME" User "Cognitive Services OpenAI User" "$FOUNDRY_ID"   # local dev with `az login`

  say "model deployment $MODEL on $FOUNDRY"
  az cognitiveservices account deployment create -n "$FOUNDRY" -g "$FOUNDRY_RG" \
    --deployment-name "$MODEL" --model-name "$MODEL" --model-version "$MODEL_VERSION" --model-format OpenAI \
    --sku-name GlobalStandard --sku-capacity "$MODEL_CAPACITY" -o none 2>/dev/null || echo "   deployment exists"

  say "key vault $KV"
  az keyvault show -n "$KV" -g "$RG" -o none 2>/dev/null \
    || az keyvault create -n "$KV" -g "$RG" -l "$LOC" --enable-rbac-authorization true -o none
  KV_ID=$(az keyvault show -n "$KV" -g "$RG" --query id -o tsv)
  assign_role "$ID_PRINCIPAL" ServicePrincipal "Key Vault Secrets User" "$KV_ID"
  assign_role "$ME" User "Key Vault Secrets Officer" "$KV_ID"

  say "log analytics $LOGS + container apps environment $ENV"
  az monitor log-analytics workspace create -n "$LOGS" -g "$RG" -l "$LOC" -o none
  if ! az containerapp env show -n "$ENV" -g "$RG" -o none 2>/dev/null; then
    az containerapp env create -n "$ENV" -g "$RG" -l "$LOC" \
      --logs-workspace-id "$(az monitor log-analytics workspace show -n "$LOGS" -g "$RG" --query customerId -o tsv)" \
      --logs-workspace-key "$(az monitor log-analytics workspace get-shared-keys -n "$LOGS" -g "$RG" --query primarySharedKey -o tsv)" -o none
  fi

  say "postgres $PG"
  if ! az postgres flexible-server show -n "$PG" -g "$RG" -o none 2>/dev/null; then
    az postgres flexible-server create -n "$PG" -g "$RG" -l "$LOC" \
      --tier Burstable --sku-name Standard_B1ms --storage-size 32 --version 16 \
      --admin-user "$PG_USER" --admin-password "$(openssl rand -base64 30 | tr -dc 'A-Za-z0-9' | head -c 32)" \
      --public-access 0.0.0.0 --yes -o none
  fi
  az postgres flexible-server db show -g "$RG" --server-name "$PG" --name "$PG_DB" -o none 2>/dev/null \
    || az postgres flexible-server db create -g "$RG" --server-name "$PG" --name "$PG_DB" -o none
  if ! az keyvault secret show --vault-name "$KV" -n database-url -o none 2>/dev/null; then
    PG_PASS=$(openssl rand -base64 30 | tr -dc 'A-Za-z0-9' | head -c 32)
    az postgres flexible-server update -n "$PG" -g "$RG" --admin-password "$PG_PASS" -o none
    az keyvault secret set --vault-name "$KV" -n database-url \
      --value "postgresql://${PG_USER}:${PG_PASS}@${PG}.postgres.database.azure.com:5432/${PG_DB}?sslmode=require" -o none
  fi

  say "linked-account OAuth secrets (Key Vault). Google values are placeholders until you paste the real client id/secret."
  for name in google-client-id google-client-secret; do
    az keyvault secret show --vault-name "$KV" -n $name -o none 2>/dev/null || az keyvault secret set --vault-name "$KV" -n $name --value unset -o none
  done

  say "admin password for the operator UI (Key Vault secret admin-password)"
  az keyvault secret show --vault-name "$KV" -n admin-password -o none 2>/dev/null \
    || az keyvault secret set --vault-name "$KV" -n admin-password --value "$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 24)" -o none

  MYIP=$(curl -s https://ifconfig.me || true)
  [ -n "$MYIP" ] && az postgres flexible-server firewall-rule create -g "$RG" -s "$PG" -n dev-"$(whoami)" \
      --start-ip-address "$MYIP" --end-ip-address "$MYIP" -o none 2>/dev/null || true
  say "infra done"
}

app() {
  if [ -z "${SKIP_BUILD:-}" ]; then
    say "build $IMAGE (cloud build, no local docker needed)"
    az acr build -r "$ACR" -g "$ACR_RG" -t "digital-employee:${TAG}" -t "digital-employee:latest" . -o none
  fi

  ID_RES=$(az identity show -n "$ID" -g "$RG" --query id -o tsv)
  ID_CLIENT=$(az identity show -n "$ID" -g "$RG" --query clientId -o tsv)
  KV_URI=$(az keyvault show -n "$KV" -g "$RG" --query properties.vaultUri -o tsv)

  if ! az containerapp show -n "$APP" -g "$RG" -o none 2>/dev/null; then
    say "create container app $APP"
    az containerapp create -n "$APP" -g "$RG" --environment "$ENV" --image "$IMAGE" \
      --registry-server "${ACR}.azurecr.io" --registry-identity "$ID_RES" --user-assigned "$ID_RES" \
      --ingress external --target-port 8080 --cpu 0.5 --memory 1Gi --min-replicas 1 --max-replicas 1 \
      --secrets "database-url=keyvaultref:${KV_URI}secrets/database-url,identityref:${ID_RES}" \
                "admin-password=keyvaultref:${KV_URI}secrets/admin-password,identityref:${ID_RES}" \
                "graph-client-secret=keyvaultref:${KV_URI}secrets/graph-client-secret,identityref:${ID_RES}" \
                "google-client-id=keyvaultref:${KV_URI}secrets/google-client-id,identityref:${ID_RES}" \
                "google-client-secret=keyvaultref:${KV_URI}secrets/google-client-secret,identityref:${ID_RES}" \
      --env-vars DATABASE_URL=secretref:database-url ADMIN_PASSWORD=secretref:admin-password AZURE_CLIENT_ID="$ID_CLIENT" \
                 MS_CLIENT_SECRET=secretref:graph-client-secret GOOGLE_CLIENT_ID=secretref:google-client-id GOOGLE_CLIENT_SECRET=secretref:google-client-secret \
                 BASE_URL="https://${APP}.$(az containerapp env show -n "$ENV" -g "$RG" --query properties.defaultDomain -o tsv)" \
                 AZURE_OPENAI_RESOURCE="$FOUNDRY" MODEL="$MODEL" PORT=8080 -o none
  else
    say "update container app $APP"
    az containerapp secret set -n "$APP" -g "$RG" --secrets \
      "graph-client-secret=keyvaultref:${KV_URI}secrets/graph-client-secret,identityref:${ID_RES}" \
      "google-client-id=keyvaultref:${KV_URI}secrets/google-client-id,identityref:${ID_RES}" \
      "google-client-secret=keyvaultref:${KV_URI}secrets/google-client-secret,identityref:${ID_RES}" -o none
    az containerapp update -n "$APP" -g "$RG" --image "$IMAGE" \
      --set-env-vars AZURE_OPENAI_RESOURCE="$FOUNDRY" MODEL="$MODEL" MS_CLIENT_SECRET=secretref:graph-client-secret \
        GOOGLE_CLIENT_ID=secretref:google-client-id GOOGLE_CLIENT_SECRET=secretref:google-client-secret \
        BASE_URL="https://$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)" -o none
  fi
  echo "https://$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)"
}

case "${1:-all}" in
  infra) infra ;;
  app) app ;;
  all) infra; app ;;
  *) echo "usage: $0 [infra|app|all]"; exit 1 ;;
esac
