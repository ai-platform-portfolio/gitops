{{/* Shared reconciliation settings; package sources and ordering are explicit. */}}
{{- define "platform.syncPolicy" -}}
# Normalize only exact AKS-owned selectors; other webhook drift stays visible.
ignoreDifferences:
  - group: admissionregistration.k8s.io
    kind: MutatingWebhookConfiguration
    jqPathExpressions:
      - >-
        .webhooks[]?.namespaceSelector | select(. == {"matchExpressions": [
        {"key": "control-plane", "operator": "NotIn", "values": ["true"]},
        {"key": "kubernetes.azure.com/managedby", "operator": "NotIn", "values": ["aks"]}]})

  - group: admissionregistration.k8s.io
    kind: ValidatingWebhookConfiguration
    jqPathExpressions:
      - >-
        .webhooks[]?.namespaceSelector | select(. == {"matchExpressions": [
        {"key": "control-plane", "operator": "NotIn", "values": ["true"]},
        {"key": "kubernetes.azure.com/managedby", "operator": "NotIn", "values": ["aks"]}]})

  # Kubernetes defaults an omitted CRD conversion strategy to None.
  # Conversion webhooks and additional fields still participate in comparison.
  - group: apiextensions.k8s.io
    kind: CustomResourceDefinition
    jqPathExpressions:
      - '.spec.conversion | select(. == {"strategy": "None"})'

syncPolicy:
  automated:
    prune: true
    selfHeal: true
  retry:
    limit: 10
    backoff:
      duration: 10s
      factor: 2
      maxDuration: 3m
  syncOptions:
    - ServerSideApply=true
    - CreateNamespace=true
    - PruneLast=true
    - RespectIgnoreDifferences=true
{{- end }}

{{/* Validate the package's GitOps inputs before accepting an enabled release. */}}
{{- define "platform.validateAtlantis" -}}
{{- $a := .Values.atlantis -}}
{{- if not (regexMatch "^[0-9]+$" (toString $a.github_app_id)) -}}
{{- fail "atlantis.github_app_id must be numeric" -}}
{{- end -}}
{{- if or (not (regexMatch "^[a-zA-Z0-9-]+$" $a.github_app_slug)) (not (regexMatch "^[a-zA-Z0-9-]+$" $a.github_team)) -}}
{{- fail "Atlantis requires GitHub App and trusted team slugs" -}}
{{- end -}}
{{- $allow := dict "positive" false -}}
{{- range $repo := required "atlantis.repositories is required" $a.repositories -}}
{{- if not (regexMatch "^!?github\\.com/[a-zA-Z0-9-]+/([a-zA-Z0-9_.-]+|\\*)$" $repo) -}}
{{- fail "Atlantis repository patterns must name an organisation; host/owner wildcards are not allowed" -}}
{{- end -}}
{{- if not (hasPrefix "!" $repo) -}}{{- $_ := set $allow "positive" true -}}{{- end -}}
{{- end -}}
{{- if not $allow.positive -}}{{- fail "Atlantis requires a positive repository pattern" -}}{{- end -}}
{{- if eq (len $a.azure.role_assignments) 0 -}}{{- fail "atlantis.azure.role_assignments must not be empty" -}}{{- end -}}
{{- range $name, $role := $a.azure.role_assignments -}}
{{- if or (gt (len $name) 54) (not (regexMatch "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$" $name)) -}}
{{- fail "Atlantis role keys must be DNS labels of at most 54 characters" -}}
{{- end -}}
{{- $_ := required "Atlantis role target resource_group_name is required" $role.resource_group_name -}}
{{- $_ := required "Atlantis role_definition_name is required" $role.role_definition_name -}}
{{- if and $role.storage_account_name (not (regexMatch "^[a-z0-9]{3,24}$" $role.storage_account_name)) -}}
{{- fail "Atlantis storage account names must be 3-24 lowercase alphanumeric characters" -}}
{{- end -}}
{{- end -}}
{{- end -}}
