url=http://sonarqube:9000
if ! curl -fsS -u "admin:$ADMIN_PASSWORD" "$url/api/authentication/validate" | grep -q '"valid":true'; then
  curl -fsS -u "admin:$CURRENT_ADMIN_PASSWORD" -X POST "$url/api/users/change_password" \
  --data-urlencode "login=admin" \
  --data-urlencode "previousPassword=$CURRENT_ADMIN_PASSWORD" \
  --data-urlencode "password=$ADMIN_PASSWORD"
fi
curl -fsS -u "admin:$ADMIN_PASSWORD" "$url/api/authentication/validate" | grep -q '"valid":true'
curl -fsS -u "admin:$ADMIN_PASSWORD" -X POST "$url/api/settings/set" \
  --data-urlencode "key=sonar.core.serverBaseURL" \
  --data-urlencode "value=https://sonarqube.{{ required "runtime.DNS_ZONE is required" .Values.runtime.DNS_ZONE }}"
setting() {
  curl -fsS -u "admin:$ADMIN_PASSWORD" -X POST "$url/api/settings/set" \
    --data-urlencode "key=$1" --data-urlencode "value=$2"
}
for group in portfolio-sonar-admins portfolio-sonar-analysts; do
  if ! curl -fsS -u "admin:$ADMIN_PASSWORD" "$url/api/user_groups/search?q=$group" | grep -q "\"name\":\"$group\""; then
    curl -fsS -u "admin:$ADMIN_PASSWORD" -X POST "$url/api/user_groups/create" --data-urlencode "name=$group"
  fi
done
for permission in admin applicationcreator gateadmin profileadmin provisioning scan; do
  curl -fsS -u "admin:$ADMIN_PASSWORD" -X POST "$url/api/permissions/add_group" \
    --data-urlencode "groupName=portfolio-sonar-admins" --data-urlencode "permission=$permission"
  curl -fsS -u "admin:$ADMIN_PASSWORD" -X POST "$url/api/permissions/remove_group" \
    --data-urlencode "groupName=Anyone" --data-urlencode "permission=$permission"
done
for permission in provisioning scan; do
  curl -fsS -u "admin:$ADMIN_PASSWORD" -X POST "$url/api/permissions/add_group" \
    --data-urlencode "groupName=portfolio-sonar-analysts" --data-urlencode "permission=$permission"
  curl -fsS -u "admin:$ADMIN_PASSWORD" -X POST "$url/api/permissions/remove_group" \
    --data-urlencode "groupName=sonar-users" --data-urlencode "permission=$permission"
done
certificate=$(printf '%s' '{{ required "runtime.SONAR_SAML_CERTIFICATE is required" .Values.runtime.SONAR_SAML_CERTIFICATE }}' | tr -d '\n\r')
setting sonar.auth.saml.applicationId '{{ required "runtime.SONAR_SAML_APPLICATION_ID is required" .Values.runtime.SONAR_SAML_APPLICATION_ID }}'
setting sonar.auth.saml.providerName 'Microsoft Entra ID'
setting sonar.auth.saml.providerId 'https://sts.windows.net/{{ required "runtime.AZURE_TENANT_ID is required" .Values.runtime.AZURE_TENANT_ID }}/'
setting sonar.auth.saml.loginUrl 'https://login.microsoftonline.com/{{ required "runtime.AZURE_TENANT_ID is required" .Values.runtime.AZURE_TENANT_ID }}/saml2'
setting sonar.auth.saml.certificate.secured "$certificate"
setting sonar.auth.saml.user.login login
setting sonar.auth.saml.user.name name
setting sonar.auth.saml.user.email email
setting sonar.auth.saml.group.name 'http://schemas.microsoft.com/ws/2008/06/identity/claims/role'
setting sonar.auth.saml.enabled true
setting sonar.forceAuthentication false
