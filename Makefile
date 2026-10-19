.DEFAULT_GOAL := help

BOOTSTRAP_DIR ?= $(CURDIR)/bootstrap
TERRAFORM ?= terraform
AZ ?= az

.PHONY: help init plan up down

# Both local users and CI identities provide an authenticated Azure CLI context.
define run_terraform
	@set -eu; \
	ARM_SUBSCRIPTION_ID="$$( $(AZ) account show --query id --output tsv --only-show-errors )"; \
	ARM_TENANT_ID="$$( $(AZ) account show --query tenantId --output tsv --only-show-errors )"; \
	test -n "$$ARM_SUBSCRIPTION_ID"; \
	test -n "$$ARM_TENANT_ID"; \
	export ARM_SUBSCRIPTION_ID ARM_TENANT_ID; \
	$(TERRAFORM) -chdir="$(BOOTSTRAP_DIR)" $(1)
endef

help:
	@printf '%s\n' \
	  'make up    Provision management infrastructure and ArgoCD' \
	  'make down  Destroy the resources managed by bootstrap' \
	  'make plan  Preview bootstrap changes' \
	  'Override the root with BOOTSTRAP_DIR=/path/to/bootstrap'

init:
	@test -f "$(BOOTSTRAP_DIR)/main.tf" || { printf 'Bootstrap root not found: %s\n' "$(BOOTSTRAP_DIR)" >&2; exit 1; }
	$(call run_terraform,init -input=false)

plan: init
	$(call run_terraform,plan -input=false)

up: init
	$(call run_terraform,apply -input=false -auto-approve)

down: init
	$(call run_terraform,destroy -input=false -auto-approve)
