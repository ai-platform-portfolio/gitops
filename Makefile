.DEFAULT_GOAL := help
.NOTPARALLEL:

BOOTSTRAP_DIR ?= $(CURDIR)/bootstrap
TERRAFORM ?= terraform
TFVARS ?= $(BOOTSTRAP_DIR)/terraform.tfvars
BACKEND_CONFIG ?= $(BOOTSTRAP_DIR)/backend.local.hcl

# Full in-cluster bootstrap, excluding Azure PostgreSQL and APIM provisioning.
AKS_TARGETS := -target=module.aks -target=module.argocd \
  -target=module.identities -target=module.aso_identity \
  -target=azurerm_storage_container.platform \
  -target=azurerm_role_assignment.ci_publisher

.PHONY: help init plan foundry-plan foundry-up foundry-agents-up postgres-plan postgres-up aks-plan aks-up up down context validate preflight

PYTHON ?= python3
export TERRAFORM

# Azure CLI supplies authentication and account context.
define run_terraform
	@subscription_id="$$(az account show --query id -o tsv --only-show-errors)" && \
	  tenant_id="$$(az account show --query tenantId -o tsv --only-show-errors)" && \
	  ARM_SUBSCRIPTION_ID="$$subscription_id" ARM_TENANT_ID="$$tenant_id" \
	  TF_VAR_foundry_spinup_date="$${FOUNDRY_SPINUP_DATE:-$$(date -u +%Y%m%d)}" \
	  $(TERRAFORM) -chdir="$(BOOTSTRAP_DIR)" $(1)
endef

# Refresh non-secret CI settings after a successful apply, including rebuilds.
define publish_foundry_config
	@endpoint="$$($(TERRAFORM) -chdir="$(BOOTSTRAP_DIR)" output -raw foundry_project_endpoint)" && \
	  project_id="$$($(TERRAFORM) -chdir="$(BOOTSTRAP_DIR)" output -raw foundry_project_id)" && \
	  location="$$($(TERRAFORM) -chdir="$(BOOTSTRAP_DIR)" output -raw foundry_location)" && \
	  organization="$$(gh repo view --json owner --jq .owner.login)" && \
	  gh variable set FOUNDRY_PROJECT_ENDPOINT --org "$$organization" --visibility all --body "$$endpoint" && \
	  gh variable set AZURE_AI_PROJECT_ID --org "$$organization" --visibility all --body "$$project_id" && \
	  gh variable set AZURE_LOCATION --org "$$organization" --visibility all --body "$$location"
endef

help:
	@printf '%s\n' \
	  'make up    Provision infrastructure, install packages and wait for health' \
	  'make down  Remove packages and destroy the disposable platform infrastructure' \
	  'make plan  Preview bootstrap changes' \
	  'make foundry-plan  Preview Foundry and its networking/identity dependencies' \
	  'make foundry-up  Provision Foundry dependencies and deploy catalog agents' \
	  'make foundry-agents-up  Retry native consumer deployment without provisioning' \
	  'make postgres-plan  Preview the private PostgreSQL server and its dependencies' \
	  'make postgres-up  Provision the private PostgreSQL server and its dependencies' \
	  'make aks-plan  Preview AKS, Argo and platform bootstrap prerequisites' \
	  'make aks-up  Provision AKS/Argo and install packages and preview automation' \
	  'make context  Select the management cluster with Azure CLI authentication' \
	  'Override the root with BOOTSTRAP_DIR=/path/to/bootstrap'

init:
	@test -f "$(TFVARS)" || { printf 'Cluster tfvars not found: %s\n' "$(TFVARS)" >&2; exit 1; }
	@test -f "$(BOOTSTRAP_DIR)/main.tf" || { printf 'Bootstrap root not found: %s\n' "$(BOOTSTRAP_DIR)" >&2; exit 1; }
	@test -f "$(BACKEND_CONFIG)" || { printf 'Backend config not found: %s; see README\n' "$(BACKEND_CONFIG)" >&2; exit 1; }
	$(call run_terraform,init -input=false -backend-config="$(BACKEND_CONFIG)")

plan: init
	$(call run_terraform,plan -input=false -var-file="$(TFVARS)")

# CI publisher grants are root resources, outside module.foundry dependencies.
foundry-plan: init
	$(call run_terraform,plan -input=false -var-file="$(TFVARS)" -target=module.foundry -target=azurerm_role_assignment.ci_publisher)

foundry-up: init
	$(call run_terraform,apply -input=false -auto-approve -var-file="$(TFVARS)" -target=module.foundry -target=azurerm_role_assignment.ci_publisher)
	$(publish_foundry_config)
	$(MAKE) foundry-agents-up

# Consumer workflows own discovery, builds, deployment and acceptance. Discover the
# existing native workflow convention, never an app list or agent definition here.
foundry-agents-up:
	@set -eu; \
	  organization="$$(gh repo view --json owner --jq .owner.login)"; \
	  repositories="$$(gh api --paginate "orgs/$$organization/repos?per_page=100" --jq '.[] | select(.archived == false and .fork == false) | .full_name')"; \
	  for repository in $$repositories; do \
	    workflow="$$(gh api --paginate "repos/$$repository/actions/workflows?per_page=100" --jq '.workflows[] | select(.path == ".github/workflows/deploy-agents.yml") | [.id, .state] | @tsv')"; \
	    test -n "$$workflow" || continue; \
	    set -- $$workflow; \
	    test "$$#" -eq 2 && test "$$2" = active || { echo "Consumer workflow is disabled: $$repository" >&2; exit 1; }; \
	    run_id="$$(gh api --method POST "repos/$$repository/actions/workflows/$$1/dispatches" \
	      -f ref=main -f 'inputs[operation]=deploy' -F return_run_details=true --jq .workflow_run_id)"; \
	    case "$$run_id" in ''|*[!0-9]*) echo "Missing deployment run ID: $$repository" >&2; exit 1;; esac; \
	    echo "Waiting for https://github.com/$$repository/actions/runs/$$run_id"; \
	    gh run watch "$$run_id" --repo "$$repository" --exit-status; \
	  done

postgres-plan: init
	$(call run_terraform,plan -input=false -var-file="$(TFVARS)" -target=module.postgres)

postgres-up: init
	$(call run_terraform,apply -input=false -auto-approve -var-file="$(TFVARS)" -target=module.postgres)

aks-plan: init
	$(call run_terraform,plan -input=false -var-file="$(TFVARS)" $(AKS_TARGETS))

aks-up: preflight init
	@command -v openssl >/dev/null || { printf 'Missing tool: openssl\n' >&2; exit 1; }
	$(call run_terraform,apply -input=false -auto-approve -var-file="$(TFVARS)" $(AKS_TARGETS))
	$(publish_foundry_config)
	$(PYTHON) helm_chart/scripts/cluster.py up --infra-dir "$(BOOTSTRAP_DIR)"
	$(MAKE) context
	$(MAKE) foundry-agents-up

up: preflight init
	@command -v openssl >/dev/null || { printf 'Missing tool: openssl\n' >&2; exit 1; }
	$(call run_terraform,apply -input=false -auto-approve -var-file="$(TFVARS)")
	$(publish_foundry_config)
	$(PYTHON) helm_chart/scripts/cluster.py up --infra-dir "$(BOOTSTRAP_DIR)"
	$(MAKE) context
	$(MAKE) foundry-agents-up

context: preflight init
	@kubeconfig="$${KUBECONFIG:-$$HOME/.kube/config}" && \
	  cluster_name="$$($(TERRAFORM) -chdir="$(BOOTSTRAP_DIR)" output -raw cluster_name)" && \
	  resource_group="$$($(TERRAFORM) -chdir="$(BOOTSTRAP_DIR)" output -raw resource_group_name)" && \
	  subscription_id="$$(az account show --query id -o tsv --only-show-errors)" && \
	  az aks get-credentials --subscription "$$subscription_id" --resource-group "$$resource_group" \
	    --name "$$cluster_name" --context "$$cluster_name" --file "$$kubeconfig" \
	    --overwrite-existing --only-show-errors && \
	  kubelogin convert-kubeconfig --kubeconfig "$$kubeconfig" --context "$$cluster_name" -l azurecli && \
	  kubectl --kubeconfig "$$kubeconfig" config use-context "$$cluster_name"

down: preflight init
	$(PYTHON) helm_chart/scripts/cluster.py down --infra-dir "$(BOOTSTRAP_DIR)" --tfvars "$(TFVARS)"
	$(call run_terraform,destroy -input=false -auto-approve -var-file="$(TFVARS)")

validate:
	$(PYTHON) -m unittest discover -s helm_chart/tests
	$(PYTHON) helm_chart/scripts/validate.py

preflight:
	@for tool in "$(TERRAFORM)" az kubectl kubelogin "$(PYTHON)"; do command -v "$$tool" >/dev/null || { printf 'Missing tool: %s\n' "$$tool" >&2; exit 1; }; done
