.DEFAULT_GOAL := help
.NOTPARALLEL:

BOOTSTRAP_DIR ?= $(CURDIR)/bootstrap
TERRAFORM ?= terraform
TFVARS ?= $(BOOTSTRAP_DIR)/terraform.tfvars
BACKEND_CONFIG ?= $(BOOTSTRAP_DIR)/backend.local.hcl

.PHONY: help init plan up down validate preflight

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
	  'make up    Provision infrastructure, install packages and wait for health' \
	  'make down  Remove packages and destroy the disposable platform infrastructure' \
	  'make plan  Preview bootstrap changes' \
	  'Override the root with BOOTSTRAP_DIR=/path/to/bootstrap'

init:
	@test -f "$(TFVARS)" || { printf 'Cluster tfvars not found: %s\n' "$(TFVARS)" >&2; exit 1; }
	@test -f "$(BOOTSTRAP_DIR)/main.tf" || { printf 'Bootstrap root not found: %s\n' "$(BOOTSTRAP_DIR)" >&2; exit 1; }
	@test -f "$(BACKEND_CONFIG)" || { printf 'Backend config not found: %s; see README\n' "$(BACKEND_CONFIG)" >&2; exit 1; }
	$(call run_terraform,init -input=false -backend-config="$(BACKEND_CONFIG)")

plan: init
	$(call run_terraform,plan -input=false -var-file="$(TFVARS)")

up: preflight init
	$(call run_terraform,apply -input=false -auto-approve -var-file="$(TFVARS)")
	$(PYTHON) helm_chart/scripts/cluster.py up --infra-dir "$(BOOTSTRAP_DIR)"

down: preflight init
	$(PYTHON) helm_chart/scripts/cluster.py down --infra-dir "$(BOOTSTRAP_DIR)"
	$(call run_terraform,destroy -input=false -auto-approve -var-file="$(TFVARS)")

validate:
	$(PYTHON) -m unittest discover -s helm_chart/tests
	$(PYTHON) helm_chart/scripts/validate.py

preflight:
	@for tool in "$(TERRAFORM)" az kubectl kubelogin "$(PYTHON)"; do command -v "$$tool" >/dev/null || { printf 'Missing tool: %s\n' "$$tool" >&2; exit 1; }; done
