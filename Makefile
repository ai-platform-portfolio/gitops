.DEFAULT_GOAL := help
INFRA_DIR ?= ../../portfolio-infra

.PHONY: help up down plan
help:
	@printf 'make up    Apply Terraform and wait for GitOps readiness\nmake down  Remove workloads, Flux and AKS; retain shared Azure resources\nmake plan  Preview Terraform changes\n'

up down plan:
	python3 scripts/cluster.py $@ --infra-dir "$(INFRA_DIR)"
