{{/* Shared reconciliation settings; package sources and ordering are explicit. */}}
{{- define "platform.syncPolicy" -}}
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
