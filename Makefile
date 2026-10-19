.DEFAULT_GOAL := help

BOOTSTRAP_DIR ?= $(CURDIR)/../bootstrap
TERRAFORM ?= terraform

.PHONY: help init plan up down

help:
	@printf '%s\n' \
	  'make up    Provision management infrastructure and ArgoCD' \
	  'make down  Destroy the resources managed by bootstrap' \
	  'make plan  Preview bootstrap changes' \
	  'Override the checkout with BOOTSTRAP_DIR=/path/to/bootstrap'

init:
	@test -f "$(BOOTSTRAP_DIR)/main.tf" || { printf 'Bootstrap root not found: %s\n' "$(BOOTSTRAP_DIR)" >&2; exit 1; }
	$(TERRAFORM) -chdir="$(BOOTSTRAP_DIR)" init -input=false

plan: init
	$(TERRAFORM) -chdir="$(BOOTSTRAP_DIR)" plan -input=false

up: init
	$(TERRAFORM) -chdir="$(BOOTSTRAP_DIR)" apply -input=false -auto-approve

down: init
	$(TERRAFORM) -chdir="$(BOOTSTRAP_DIR)" destroy -input=false -auto-approve
