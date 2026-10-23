.DEFAULT_GOAL := help
.NOTPARALLEL:

BOOTSTRAP_DIR ?= $(CURDIR)/bootstrap
TERRAFORM ?= terraform
CLUSTER ?= management
WORKSPACE := $(if $(filter management,$(CLUSTER)),default,$(CLUSTER))
LOCAL_TFVARS := $(shell if test -f "$(BOOTSTRAP_DIR)/local.tfvars"; then printf yes; fi)
TFVARS ?= $(if $(filter management,$(CLUSTER)),$(BOOTSTRAP_DIR)/$(if $(LOCAL_TFVARS),local,terraform).tfvars,$(CURDIR)/clusters/$(CLUSTER)/terraform.tfvars)

.PHONY: help init plan up down preflight

PYTHON ?= python3
export TERRAFORM

# Azure CLI supplies authentication and account context.
define run_terraform
	@subscription_id="$$(az account show --query id -o tsv --only-show-errors)" && \
	  tenant_id="$$(az account show --query tenantId -o tsv --only-show-errors)" && \
	  ARM_SUBSCRIPTION_ID="$$subscription_id" ARM_TENANT_ID="$$tenant_id" $(TERRAFORM) -chdir="$(BOOTSTRAP_DIR)" $(1)
endef

help:
	@printf '%s\n' \
	  'make up    Provision AKS, managed identities and the initial ArgoCD release' \
	  'make down  Destroy bootstrap infrastructure before platform workloads are installed' \
	  'make plan  Preview bootstrap changes' \
	  'Workload: CLUSTER=dev TFVARS=/path/to/settings.tfvars' \
	  'Override the root with BOOTSTRAP_DIR=/path/to/bootstrap'

init:
	@test -f "$(TFVARS)" || { printf 'Cluster tfvars not found: %s\n' "$(TFVARS)" >&2; exit 1; }
	@test -f "$(BOOTSTRAP_DIR)/main.tf" || { printf 'Bootstrap root not found: %s\n' "$(BOOTSTRAP_DIR)" >&2; exit 1; }
	$(TERRAFORM) -chdir="$(BOOTSTRAP_DIR)" init -input=false
	@$(TERRAFORM) -chdir="$(BOOTSTRAP_DIR)" workspace select "$(WORKSPACE)" || $(TERRAFORM) -chdir="$(BOOTSTRAP_DIR)" workspace new "$(WORKSPACE)"

plan: init
	$(call run_terraform,plan -input=false -var-file="$(TFVARS)")

up: preflight init
	$(PYTHON) scripts/prepare_storage.py --infra-dir "$(BOOTSTRAP_DIR)" --tfvars "$(TFVARS)"
	$(call run_terraform,apply -input=false -auto-approve -var-file="$(TFVARS)")

down: preflight init
	$(call run_terraform,destroy -input=false -auto-approve -var-file="$(TFVARS)")

preflight:
	@for tool in "$(TERRAFORM)" az kubelogin "$(PYTHON)"; do command -v "$$tool" >/dev/null || { printf 'Missing tool: %s\n' "$$tool" >&2; exit 1; }; done
