"""Reconcile a reviewed workload request after Atlantis creates its AKS.

Runs inside management AKS with a dedicated Workload Identity. It never reads
Terraform state or gives Atlantis Kubernetes credentials. All Azure service
resources are Argo/ASO or Crossplane owned; credentials stay in Kubernetes/KV.
"""
import base64
import copy
import ipaddress
import json
import os
import re
import socket
import tempfile
import time
from urllib.parse import urlparse

import requests
import yaml
from azure.identity import WorkloadIdentityCredential
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from credentials import prepare as prepare_credentials


ARM = "https://management.azure.com/.default"
AKS = "6dae42f8-4368-4678-94ff-3960e28e3630/.default"
NAME = re.compile(r"^aks-[a-z0-9]+(?:-[a-z0-9]+)*$")
NAMESPACE = "argocd"
FOUNDATION_NAMESPACE = "cluster-onboarding"
MANAGED = "platform.ai-platform-portfolio/managed"


def retry(check, label, seconds=3600):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (ApiException, requests.RequestException) as error:
            last = str(error)[:300]
        time.sleep(15)
    raise TimeoutError(f"{label}: {last or 'not ready'}")


def ready(object_):
    meta = object_.get("metadata", {})
    generation = meta.get("generation")
    return any(c.get("type") == "Ready" and c.get("status") == "True"
               and c.get("observedGeneration", generation) == generation
               for c in object_.get("status", {}).get("conditions", []))


def app_ready(app):
    spec = app.get("spec", {})
    status = app.get("status", {})
    compared = status.get("sync", {}).get("comparedTo", {})
    return (status.get("sync", {}).get("status") == "Synced"
            and status.get("health", {}).get("status") == "Healthy"
            and (not spec.get("source") or compared.get("source") == spec["source"])
            and (not spec.get("sources") or compared.get("sources") == spec["sources"]))


class Kube:
    def __init__(self, api_client):
        self.core = client.CoreV1Api(api_client)
        self.custom = client.CustomObjectsApi(api_client)

    def object(self, kind, name, namespace):
        try:
            if kind == "secret":
                return self.core.read_namespaced_secret(name, namespace).to_dict()
            if kind == "configmap":
                return self.core.read_namespaced_config_map(name, namespace).to_dict()
            if kind == "applications.argoproj.io":
                return self.custom.get_namespaced_custom_object("argoproj.io", "v1alpha1", namespace, "applications", name)
            if kind == "clusters.postgresql.cnpg.io":
                return self.custom.get_namespaced_custom_object("postgresql.cnpg.io", "v1", namespace, "clusters", name)
            raise ValueError(kind)
        except ApiException as error:
            if error.status == 404:
                return None
            raise

    def objects(self, kind, namespace):
        if kind == "persistentvolumeclaims":
            return self.core.list_namespaced_persistent_volume_claim(namespace).items
        if kind == "applications.argoproj.io":
            return self.custom.list_namespaced_custom_object("argoproj.io", "v1alpha1", namespace, "applications")["items"]
        raise ValueError(kind)

    def manifest(self, doc):
        kind = doc["kind"]
        name = doc["metadata"]["name"]
        namespace = doc["metadata"].get("namespace")
        if kind == "Namespace":
            try:
                self.core.create_namespace(doc)
            except ApiException as error:
                if error.status != 409:
                    raise
            return
        if kind in {"Secret", "ConfigMap"}:
            create = self.core.create_namespaced_secret if kind == "Secret" else self.core.create_namespaced_config_map
            patch = self.core.patch_namespaced_secret if kind == "Secret" else self.core.patch_namespaced_config_map
            try:
                create(namespace, doc)
            except ApiException as error:
                if error.status != 409:
                    raise
                patch(name, namespace, doc)
            return
        if kind == "Application":
            try:
                self.custom.create_namespaced_custom_object("argoproj.io", "v1alpha1", namespace, "applications", doc)
            except ApiException as error:
                if error.status != 409:
                    raise
                self.custom.patch_namespaced_custom_object("argoproj.io", "v1alpha1", namespace, "applications", name, doc)
            return
        raise ValueError(kind)


def in_cluster():
    config.load_incluster_config()
    return Kube(client.ApiClient())


def remote(kubeconfig, credential):
    doc = yaml.safe_load(base64.b64decode(kubeconfig))
    context = next(c for c in doc["contexts"] if c["name"] == doc["current-context"])
    cluster_name = context["context"]["cluster"]
    cluster = next(c["cluster"] for c in doc["clusters"] if c["name"] == cluster_name)
    ca = base64.b64decode(cluster["certificate-authority-data"])
    file = tempfile.NamedTemporaryFile(prefix="aks-ca-", delete=False)
    file.write(ca)
    file.close()
    configuration = client.Configuration()
    configuration.host = cluster["server"]
    configuration.verify_ssl = True
    configuration.ssl_ca_cert = file.name
    configuration.api_key_prefix["authorization"] = "Bearer"
    configuration.refresh_api_key_hook = lambda c: c.api_key.update(
        authorization=credential.get_token(AKS).token)
    configuration.refresh_api_key_hook(configuration)
    return Kube(client.ApiClient(configuration)), cluster


def arm(credential, method, url):
    response = requests.request(method, "https://management.azure.com" + url,
                                headers={"Authorization": "Bearer " + credential.get_token(ARM).token},
                                timeout=30)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def application(name, server, repo, revision, path, values):
    return {"apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
            "metadata": {"name": name, "namespace": NAMESPACE, "labels": {MANAGED: "true"}},
            "spec": {"project": "default", "destination": {"server": server, "namespace": NAMESPACE},
                     "source": {"repoURL": repo, "targetRevision": revision, "path": path,
                                "helm": {"valuesObject": values}},
                     "syncPolicy": {"automated": {"prune": True, "selfHeal": True},
                                    "syncOptions": ["CreateNamespace=true", "ServerSideApply=true"]}}}


def status(hub, name, phase, detail=""):
    hub.manifest({"apiVersion": "v1", "kind": "ConfigMap",
                  "metadata": {"name": "workload-status-" + name, "namespace": NAMESPACE,
                               "labels": {"platform.ai-platform-portfolio/workload-status": "true"}},
                  "data": {"phase": phase, "detail": detail[:400]}})


def foundation_values(name, profile, aks, hub_runtime, settings, hub_config):
    vault_uri = hub_runtime["AZURE_KEY_VAULT_URI"]
    return {"cluster": {"name": name, "resourceGroup": profile["cluster"]["resourceGroup"],
                        "location": profile["cluster"]["location"],
                        "aksId": aks["id"], "oidcIssuer": aks["properties"]["oidcIssuerProfile"]["issuerUrl"]},
            "shared": {"subscriptionId": hub_runtime["AZURE_SUBSCRIPTION_ID"],
                       "tenantId": hub_runtime["AZURE_TENANT_ID"],
                       "storageAccount": hub_runtime["AZURE_STORAGE_ACCOUNT"],
                       "storageResourceGroup": settings["testkube"]["azure"]["storageResourceGroup"],
                       "dnsZone": hub_runtime["AZURE_DNS_ZONE"],
                       "dnsResourceGroup": hub_runtime["AZURE_DNS_RESOURCE_GROUP"],
                       "vaultName": urlparse(vault_uri).hostname.split(".")[0],
                       "vaultUri": vault_uri,
                       "vaultResourceGroup": settings["clusterOnboarding"]["vaultResourceGroup"],
                       "foundryName": settings["holmes"]["azure"]["foundryResourceName"],
                       "foundryResourceGroup": settings["holmes"]["azure"]["foundryResourceGroupName"]},
            "onboarding": {"workerPrincipalId": hub_runtime["ONBOARDING_PRINCIPAL_ID"],
                           "managementArgoPrincipalId": json.loads(hub_config["identity"])["principal_id"]},
            "identity": {"ownerPrincipalId": hub_runtime["CROSSPLANE_PRINCIPAL_ID"],
                         "langfuseUsers": settings["langfuse"]["sso"]["users"],
                         "langfuseCredentialValidityDays": settings["langfuse"]["sso"]["credentialValidityDays"],
                         "devlakeUsers": settings["devlake"]["users"],
                         "devlakeCredentialValidityDays": settings["devlake"]["credentialValidityDays"],
                         "grafanaUsers": settings["langfuse"]["sso"]["users"],
                         "grafanaCredentialValidityDays": 180,
                         "sonarqubeUsers": settings["langfuse"]["sso"]["users"]}}


def foundation_ready(hub, name):
    app = hub.object("applications.argoproj.io", "foundation-" + name, NAMESPACE)
    if not app or not app_ready(app):
        return False
    for identity in ("platform", "postgres", "aso"):
        cm = hub.object("configmap", f"foundation-{name}-{identity}", FOUNDATION_NAMESPACE)
        if not cm or not cm["data"].get("clientId"):
            return False
    for service in ("langfuse", "devlake", "grafana"):
        secret = hub.object("secret", f"{name}-{service}-sso", FOUNDATION_NAMESPACE)
        if not secret or not secret.get("data", {}).get("client-id"):
            return False
    sonar = hub.custom.get_namespaced_custom_object(
        "applications.azuread.m.upbound.io", "v1beta1", FOUNDATION_NAMESPACE,
        "applications", name + "-sonarqube-saml")
    return sonar if ready(sonar) and sonar.get("status", {}).get("atProvider", {}).get("clientId") else False


def runtime_for(name, profile, aks, hub_runtime, hub):
    result = copy.deepcopy(hub_runtime)
    result["CLUSTER_NAME"] = name
    result["CLUSTER_RESOURCE_GROUP"] = profile["cluster"]["resourceGroup"]
    result["DNS_ZONE"] = name + "." + hub_runtime["AZURE_DNS_ZONE"]
    result["ARGOCD_HOSTNAME"] = "argocd." + result["DNS_ZONE"]
    result["AKS_OIDC_ISSUER_URL"] = aks["properties"]["oidcIssuerProfile"]["issuerUrl"]
    for identity, key in (("platform", "AZURE_CLIENT_ID"), ("postgres", "POSTGRES_AZURE_CLIENT_ID"),
                          ("aso", "ASO_CLIENT_ID")):
        result[key] = hub.object("configmap", f"foundation-{name}-{identity}", FOUNDATION_NAMESPACE)["data"]["clientId"]
    for suffix, key in (("postgres-backups", "POSTGRES"), ("loki-chunks", "LOKI_CHUNKS"),
                        ("loki-ruler", "LOKI_RULER"), ("mimir-blocks", "MIMIR_BLOCKS"),
                        ("mimir-ruler", "MIMIR_RULER"), ("mimir-alertmanager", "MIMIR_ALERTMANAGER"),
                        ("tempo-traces", "TEMPO_TRACES")):
        result["STORAGE_" + key] = name + "-" + suffix
    sonar = hub.custom.get_namespaced_custom_object(
        "applications.azuread.m.upbound.io", "v1beta1", FOUNDATION_NAMESPACE,
        "applications", name + "-sonarqube-saml")
    result["SONAR_SAML_APPLICATION_ID"] = "api://" + hub_runtime["AZURE_TENANT_ID"] + "/sonarqube-" + name
    result["SONAR_SAML_METADATA_APP_ID"] = sonar["status"]["atProvider"]["clientId"]
    result.pop("SONAR_SAML_CERTIFICATE", None)
    return result


def register(hub, name, cluster, hub_config, stage="bootstrap", root=None):
    identity = json.loads(hub_config["identity"])
    config_value = {"execProviderConfig": {"command": "argocd-k8s-auth", "args": ["azure"],
                                           "apiVersion": "client.authentication.k8s.io/v1beta1",
                                           "env": {"AAD_LOGIN_METHOD": "workloadidentity",
                                                   "AZURE_CLIENT_ID": identity["client_id"],
                                                   "AZURE_TENANT_ID": hub_config["tenant_id"],
                                                   "AZURE_FEDERATED_TOKEN_FILE": "/var/run/secrets/azure/tokens/azure-identity-token"}},
                    "tlsClientConfig": {"insecure": False,
                                        "caData": cluster["certificate-authority-data"]}}
    labels = {"argocd.argoproj.io/secret-type": "cluster",
              "platform.ai-platform-portfolio/role": "workload",
              "platform.ai-platform-portfolio/stage": stage}
    annotations = {"platform.ai-platform-portfolio/root": json.dumps(root)} if root else {}
    hub.manifest({"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
                  "metadata": {"name": "cluster-" + name, "namespace": NAMESPACE,
                               "labels": labels, "annotations": annotations},
                  "stringData": {"name": name, "server": cluster["server"],
                                 "config": json.dumps(config_value)}})


def child_argocd(hub, child, name, cluster, git, configmap):
    child.manifest({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NAMESPACE}})
    customizations = {k: v for k, v in configmap.items() if k.startswith("resource.customizations.")}
    customizations.update({"admin.enabled": "false", "users.anonymous.enabled": "false"})
    child.manifest({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "argocd-cm", "namespace": NAMESPACE,
                   "labels": {"app.kubernetes.io/name": "argocd-cm", "app.kubernetes.io/part-of": "argocd"}},
                   "data": customizations})
    app = application("bootstrap-" + name, cluster["server"],
                      "https://argoproj.github.io/argo-helm", "10.10.5", "", {})
    app["spec"]["source"] = {"repoURL": "https://argoproj.github.io/argo-helm", "chart": "argo-cd",
                             "targetRevision": "10.10.5", "helm": {"releaseName": "argocd", "valuesObject": {
                                 "fullnameOverride": "argocd", "configs": {"cm": {"create": False},
                                 "params": {"server.insecure": True},
                                 "rbac": {"policy.default": "", "policy.csv": ""},
                                 "secret": {"createSecret": True}},
                                 "server": {"service": {"type": "ClusterIP"}},
                                 "dex": {"enabled": False}}}}
    hub.manifest(app)
    retry(lambda: app_ready(hub.object("applications.argoproj.io", app["metadata"]["name"], NAMESPACE) or {}),
          "child Argo CD", 3600)


def child_platform(child, name, runtime, settings, git, sonar_ready):
    atlantis = {**settings["atlantis"], "enabled": False}
    backstage = {**settings["backstage"], "enabled": False,
                 "clusterRequests": {"enabled": False}}
    root = application("platform", "https://kubernetes.default.svc", git["repository"],
                       git["revision"], "helm_chart/platform",
                       {"cluster": {"name": name, "role": "workload"}, "git": git,
                        "runtime": runtime, "sonarqubeReady": sonar_ready,
                        "holmes": settings["holmes"], "backstage": backstage,
                        "atlantis": atlantis, "langfuse": settings["langfuse"],
                        "testkube": settings["testkube"], "devlake": settings["devlake"]})
    child.manifest(root)
    return root


def cidrs_for_host(url, private=False):
    host = urlparse(url).hostname
    addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    result = sorted(f"{address}/32" for address in addresses if ipaddress.ip_address(address).version == 4
                    and (not private or ipaddress.ip_address(address).is_private))
    if not result:
        raise RuntimeError(f"No {'private ' if private else ''}IPv4 address for {host}")
    return result


def workload_settings(settings, cluster):
    result = copy.deepcopy(settings)
    if result["holmes"]["enabled"]:
        result["holmes"]["apiCidrs"] = cidrs_for_host(cluster["server"])
        result["holmes"]["modelCidrs"] = cidrs_for_host(result["holmes"]["modelEndpoint"], private=True)
        result["holmes"]["entraCidrs"] = result["holmes"]["entraCidrs"] or result["entraPrefixes"]
        if not result["holmes"]["entraCidrs"]:
            raise RuntimeError("Entra token endpoint egress is not configured")
    return result


def platform_ready(child, sonar_ready):
    root = child.object("applications.argoproj.io", "platform", NAMESPACE)
    if not root or not root.get("status", {}).get("resources") or not app_ready(root):
        return False
    expected = {r["name"] for r in root["status"]["resources"] if r["kind"] == "Application"}
    required = {"sonarqube", "sonarqube-configure"} if sonar_ready else {"postgres"}
    if not required <= expected:
        return False
    found = {a["metadata"]["name"]: a for a in child.objects("applications.argoproj.io", NAMESPACE)}
    return bool(expected) and expected <= found.keys() and all(app_ready(found[n]) for n in expected)


def prepare_sonar(child):
    retry(lambda: ready(child.object("clusters.postgresql.cnpg.io", "platform", "postgres") or {}),
          "PostgreSQL", 3600)
    role = child.object("secret", "sonarqube-role", "postgres")["data"]
    ca = child.object("secret", "platform-ca", "postgres")["data"]["ca.crt"]
    child.manifest({"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
                    "metadata": {"name": "sonarqube-jdbc", "namespace": "sonarqube"},
                    "data": {"jdbc-password": role["password"], "ca.crt": ca}})


def endpoints_ready(runtime, settings):
    services = ["grafana", "langfuse", "sonarqube"]
    if settings["devlake"]["enabled"]:
        services.append("devlake")
    for service in services:
        hostname = service + "." + runtime["DNS_ZONE"]
        response = requests.get("https://" + hostname + "/", allow_redirects=False, timeout=10)
        if not (200 <= response.status_code < 400 or response.status_code in (401, 403)):
            return False
    return True


def process(hub, credential, request, platform, hub_config, argocd_config):
    profile = yaml.safe_load(request["data"]["profile.yaml"])
    name = profile["cluster"]["name"]
    if not NAME.fullmatch(name) or profile["cluster"]["resourceGroup"] != "rg-" + name:
        raise ValueError("Invalid reviewed cluster request")
    runtime = platform["runtime"]
    subscription = runtime["AZURE_SUBSCRIPTION_ID"]
    aks_url = (f"/subscriptions/{subscription}/resourceGroups/rg-{name}"
               f"/providers/Microsoft.ContainerService/managedClusters/{name}?api-version=2024-10-01")
    aks = arm(credential, "GET", aks_url)
    if not aks or aks["properties"].get("provisioningState") != "Succeeded":
        status(hub, name, "WaitingForAtlantis", "AKS has not reached Succeeded")
        return
    properties = aks["properties"]
    if not (properties.get("disableLocalAccounts") is True
            and properties.get("aadProfile", {}).get("enableAzureRBAC") is True
            and properties.get("oidcIssuerProfile", {}).get("issuerUrl", "").startswith("https://")):
        raise RuntimeError("AKS must disable local accounts and enable Entra Azure RBAC and OIDC")
    git = platform["git"]
    settings = platform["settings"]
    values = foundation_values(name, profile, aks, runtime, settings, hub_config)
    foundation = application("foundation-" + name, "https://kubernetes.default.svc",
                             git["repository"], git["revision"], "helm_chart/cluster-foundation", values)
    foundation["metadata"]["finalizers"] = ["resources-finalizer.argocd.argoproj.io"]
    hub.manifest(foundation)
    status(hub, name, "Foundation", "Waiting for GitOps-owned Azure and Entra dependencies")
    retry(lambda: foundation_ready(hub, name), "cluster foundation", 7200)
    creds = arm(credential, "POST", aks["id"] + "/listClusterUserCredential?api-version=2024-10-01")
    if not creds or len(creds.get("kubeconfigs", [])) != 1:
        raise RuntimeError("AKS did not return one cluster user kubeconfig")
    child, cluster = remote(creds["kubeconfigs"][0]["value"], credential)
    retry(lambda: child.core.list_namespace(limit=1), "AKS Azure RBAC propagation", 1800)
    registration = hub.object("secret", "cluster-" + name, NAMESPACE)
    if not registration or registration["metadata"]["labels"].get("platform.ai-platform-portfolio/stage") != "ready":
        register(hub, name, cluster, hub_config)
    status(hub, name, "ArgoCD", "Installing the local GitOps controller")
    child_argocd(hub, child, name, cluster, git, argocd_config)
    prepare_credentials(child)
    child_runtime = runtime_for(name, profile, aks, runtime, hub)
    settings = workload_settings(settings, cluster)
    status(hub, name, "Platform", "Reconciling platform services")
    root = child_platform(child, name, child_runtime, settings, git, False)
    retry(lambda: platform_ready(child, False), "platform foundations", 7200)
    prepare_sonar(child)
    root = child_platform(child, name, child_runtime, settings, git, True)
    retry(lambda: platform_ready(child, True), "full platform", 7200)
    retry(lambda: endpoints_ready(child_runtime, settings), "public DNS and TLS endpoints", 1800)
    register(hub, name, cluster, hub_config, "ready", root)
    status(hub, name, "Healthy", "Current root and every child Application are Synced and Healthy")


def main():
    hub = in_cluster()
    credential = WorkloadIdentityCredential()
    root = hub.object("applications.argoproj.io", "platform", NAMESPACE)
    if not root:
        raise RuntimeError("Management platform root is unavailable")
    values = root["spec"]["source"]["helm"]["valuesObject"]
    settings = json.loads(hub.object("configmap", "workload-onboarding-settings", NAMESPACE)["data"]["values.json"])
    platform = {"runtime": values["runtime"], "git": values["git"], "settings": settings}
    hub_config = hub.object("configmap", "platform-hub", NAMESPACE)["data"]
    argocd_config = hub.object("configmap", "argocd-cm", NAMESPACE)["data"]
    requests_ = hub.core.list_namespaced_config_map(
        NAMESPACE, label_selector="platform.ai-platform-portfolio/workload-request=true").items
    target = os.environ.get("CLUSTER_NAME")
    if target:
        requests_ = [r for r in requests_ if r.metadata.labels.get("platform.ai-platform-portfolio/cluster") == target]
        if len(requests_) != 1:
            raise RuntimeError("The named GitOps workload request is missing or duplicated")
    for request in requests_:
        doc = request.to_dict()
        name = doc["metadata"]["labels"]["platform.ai-platform-portfolio/cluster"]
        try:
            process(hub, credential, doc, platform, hub_config, argocd_config)
        except Exception as error:
            status(hub, name, "Error", str(error))
            raise


if __name__ == "__main__":
    main()
