{{/* Native Argo specs remain the interface; only shared installation defaults live here. */}}
{{- define "platform.eventSource" -}}
{{- $spec := deepCopy .spec -}}
{{- range $name, $webhook := default dict $spec.webhook -}}
{{- $_ := required (printf "webhook %s requires an explicit authSecret; no anonymous receiver or generated permanent token" $name) $webhook.authSecret -}}
{{- $_ := required "webhook authSecret.name is required" $webhook.authSecret.name -}}
{{- $_ := required "webhook authSecret.key is required" $webhook.authSecret.key -}}
{{- end -}}
{{- $defaults := dict "eventBusName" "platform" "template" (dict "serviceAccountName" "platform-event-source" "nodeSelector" (dict "agentpool" "platform") "container" (dict "resources" (dict "requests" (dict "cpu" "10m" "memory" "64Mi") "limits" (dict "memory" "256Mi")))) -}}
apiVersion: argoproj.io/v1alpha1
kind: EventSource
metadata:
  name: {{ .name }}
  namespace: cluster-onboarding
  annotations:
    argocd.argoproj.io/sync-wave: "30"
spec:
{{ mergeOverwrite $defaults $spec | toYaml | indent 2 }}
{{- end -}}

{{- define "platform.eventSensor" -}}
{{- $defaults := dict "eventBusName" "platform" "template" (dict "serviceAccountName" "platform-event-sensor" "nodeSelector" (dict "agentpool" "platform") "container" (dict "resources" (dict "requests" (dict "cpu" "10m" "memory" "64Mi") "limits" (dict "memory" "256Mi")))) -}}
apiVersion: argoproj.io/v1alpha1
kind: Sensor
metadata:
  name: {{ .name }}
  namespace: cluster-onboarding
  annotations:
    argocd.argoproj.io/sync-wave: "30"
spec:
{{ mergeOverwrite $defaults (deepCopy .spec) | toYaml | indent 2 }}
{{- end -}}
