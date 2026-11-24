{{- define "platform.hostedGeneration" -}}
{{- printf "%s/%s/%s" .Values.runtime.AKS_OIDC_ISSUER_URL .Values.runtime.FOUNDRY_ACCOUNT_NAME .Values.argocd.onboarding.dependencies.model.project | lower | sha256sum | trunc 24 -}}
{{- end -}}

{{- define "platform.hostedGatewayDefaults" -}}
{{- $values := .Values -}}
{{- $gateway := $values.litellm -}}
{{- $policies := deepCopy $gateway.access.policies -}}
{{- if not (and $gateway.bedrock.enabled $values.runtime.AWS_GATEWAY_ROLE_ARN) -}}
{{- $_ := unset $policies "bounded-claude" -}}
{{- $_ := unset $policies "bounded-reasoning-coding" -}}
{{- end -}}
{{- if not (and $gateway.google.enabled $values.runtime.GOOGLE_GATEWAY_PROVIDER $values.runtime.GOOGLE_GATEWAY_PROJECT $values.runtime.GOOGLE_GATEWAY_ACCOUNT) -}}
{{- $_ := unset $policies "bounded-gemini" -}}
{{- end -}}
modelGateway:
  endpoint: {{ printf "https://%s/v1" (tpl $gateway.access.hostname .) | quote }}
  vaultUri: {{ trimSuffix "/" $values.runtime.AZURE_KEY_VAULT_URI | quote }}
  masterSecret: {{ printf "platform-%s-litellm-master" $values.cluster.name | quote }}
  namespace: litellm
  issuerServiceAccount: litellm-key-writer
  issuerIdentitySecret: litellm-key-writer
  image: {{ printf "%s:%s" $gateway.image.repository $gateway.image.tag | quote }}
  policies: {{ $policies | toJson }}
azure:
  subscriptionId: {{ $values.runtime.AZURE_SUBSCRIPTION_ID | quote }}
  tenantId: {{ $values.runtime.AZURE_TENANT_ID | quote }}
  resourceNamespace: azure-resources
  keyVault:
    name: {{ first (splitList "." (trimPrefix "https://" $values.runtime.AZURE_KEY_VAULT_URI)) | quote }}
    resourceGroup: {{ $gateway.keyVault.resourceGroup | quote }}
{{- end -}}
