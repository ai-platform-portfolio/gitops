#!/usr/bin/env python3
"""Build Flux phases, render pinned charts, and check CRDs and dependencies offline."""

import argparse
import json
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

ROOT = Path(__file__).resolve().parents[1]
VARIABLE = re.compile(r"\$\{([A-Z_]+)\}")
GUID = "00000000-0000-0000-0000-000000000001"
SETTINGS = {
    "AZURE_TENANT_ID": GUID,
    "AZURE_SUBSCRIPTION_ID": GUID,
    "AZURE_DNS_CLIENT_ID": GUID,
    "AZURE_KEY_VAULT_CLIENT_ID": GUID,
    "AZURE_STORAGE_CLIENT_ID": GUID,
    "AZURE_STORAGE_ACCOUNT": "gitopsvalidation",
}


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def load(text, substitute=True):
    def replace(match):
        assert match[1] in SETTINGS, f"Unknown bootstrap setting: {match[1]}"
        return SETTINGS[match[1]]

    if substitute:
        text = VARIABLE.sub(replace, text)
    return [doc for doc in yaml.safe_load_all(text) if doc]


def identity(doc):
    meta = doc["metadata"]
    return doc["kind"], meta.get("namespace", ""), meta["name"]


def check_graph(nodes, edges):
    visiting, done, ordered = set(), set(), []

    def visit(node):
        assert node in nodes, f"Missing dependency: {node}"
        assert node not in visiting, f"Dependency cycle: {node}"
        if node in done:
            return
        visiting.add(node)
        for dependency in edges(node):
            visit(dependency)
        visiting.remove(node)
        done.add(node)
        ordered.append(node)

    for node in nodes:
        visit(node)
    return ordered


def validate(cache):
    chart_dir = cache / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    for path in (ROOT / "flux").rglob("*.yaml"):
        load(path.read_text())
    root = load(run("kubectl", "kustomize", str(ROOT / "flux")))
    phases = {doc["metadata"]["name"]: doc for doc in root if doc["kind"] == "Kustomization"}
    check_graph(phases, lambda name: [dep["name"] for dep in phases[name]["spec"].get("dependsOn", [])])

    objects, owners = [], {}
    for name, phase in phases.items():
        for doc in load(run("kubectl", "kustomize", str(ROOT / phase["spec"]["path"]))):
            key = identity(doc)
            assert key not in owners, f"{key} is owned by both {owners.get(key)} and {name}"
            owners[key] = name
            objects.append(doc)
    repositories = {identity(doc)[1:]: doc for doc in objects if doc["kind"] == "HelmRepository"}
    releases = {identity(doc)[1:]: doc for doc in objects if doc["kind"] == "HelmRelease"}
    release_order = check_graph(releases, lambda key: [
        (dep.get("namespace", key[0]), dep["name"])
        for dep in releases[key]["spec"].get("dependsOn", [])
    ])

    flux_schema = cache / "install.yaml"
    if not flux_schema.exists():
        urllib.request.urlretrieve(
            "https://github.com/fluxcd/flux2/releases/download/v2.9.6/install.yaml", flux_schema
        )
    schemas, producers, rendered = {}, {}, []
    crd_releases, rendered_releases = {}, []

    def register(docs, producer):
        for doc in docs:
            if doc["kind"] != "CustomResourceDefinition":
                continue
            spec = doc["spec"]
            for version in spec["versions"]:
                key = (spec["group"] + "/" + version["name"], spec["names"]["kind"])
                schemas[key] = version["schema"]["openAPIV3Schema"]
                producers[key] = producer

    register(load(flux_schema.read_text()), "bootstrap")
    for key in release_order:
        release = releases[key]
        chart = release["spec"]["chart"]["spec"]
        ref = chart["sourceRef"]
        repo = repositories[(ref.get("namespace", key[0]), ref["name"])]
        path = chart_dir / chart["chart"]
        if not path.exists():
            url = repo["spec"]["url"]
            source = (url + "/" + chart["chart"]) if url.startswith("oci://") else chart["chart"]
            args = ["helm", "pull", source, "--version", chart["version"], "--untar", "--untardir", str(chart_dir)]
            if not url.startswith("oci://"):
                args += ["--repo", url]
            run(*args)
        actual = yaml.safe_load((path / "Chart.yaml").read_text())["version"]
        assert actual.lstrip("v") == chart["version"].lstrip("v"), f"Wrong cached chart version: {key}"
        values = cache / (key[1] + "-values.yaml")
        values.write_text(yaml.safe_dump(release["spec"].get("values", {})))
        lint_args = ["--set", "clickhouse.crdCheck=false"] if chart["chart"] == "langfuse" else []
        run("helm", "lint", str(path), "-f", str(values), "--kube-version", "1.34.0", *lint_args)
        capabilities = [arg for api, kind in schemas for arg in ("--api-versions", api + "/" + kind)]
        docs = load(run(
            "helm", "template", release["spec"].get("releaseName", "-".join(key)), str(path),
            "--namespace", key[0], "--include-crds", "--kube-version", "1.34.0", "-f", str(values), *capabilities
        ), substitute=False)
        register(docs, owners[("HelmRelease", *key)])
        if chart["chart"] == "opentelemetry-collector":
            config = next(doc["data"]["relay"] for doc in docs
                          if doc["kind"] == "ConfigMap" and "relay" in doc.get("data", {}))
            (cache / "otel-config.yaml").write_text(config)
        for doc in docs:
            if doc["kind"] == "CustomResourceDefinition":
                for version in doc["spec"]["versions"]:
                    crd_releases[(doc["spec"]["group"] + "/" + version["name"],
                                  doc["spec"]["names"]["kind"])] = key
        rendered_releases.extend((doc, key) for doc in docs)
        rendered.extend(docs)

    def ancestors(name):
        result = set()
        for dep in phases[name]["spec"].get("dependsOn", []):
            result.add(dep["name"])
            result.update(ancestors(dep["name"]))
        return result

    for doc in root + objects + rendered:
        key = doc["apiVersion"], doc["kind"]
        if key in schemas:
            errors = list(Draft7Validator(schemas[key]).iter_errors(doc))
            assert not errors, f"{identity(doc)}: {errors[0].message if errors else ''}"
        elif doc["apiVersion"] not in ("v1", "apps/v1", "batch/v1", "rbac.authorization.k8s.io/v1",
                                      "policy/v1", "autoscaling/v2", "apiextensions.k8s.io/v1",
                                      "admissionregistration.k8s.io/v1", "networking.k8s.io/v1"):
            raise AssertionError(f"No CRD schema found: {key}")
    for doc in objects:
        key = doc["apiVersion"], doc["kind"]
        if key in producers and producers[key] != "bootstrap":
            owner = owners[identity(doc)]
            assert producers[key] in ancestors(owner), f"{identity(doc)} can start before its CRD exists"
        if doc["kind"] == "ConfigMap" and "azure.json" in doc.get("data", {}):
            assert json.loads(doc["data"]["azure.json"])["useWorkloadIdentityExtension"] is True

    def release_ancestors(key):
        result = set()
        for dep in releases[key]["spec"].get("dependsOn", []):
            dependency = (dep.get("namespace", key[0]), dep["name"])
            result.add(dependency)
            result.update(release_ancestors(dependency))
        return result

    for doc, release_key in rendered_releases:
        producer = crd_releases.get((doc["apiVersion"], doc["kind"]))
        if producer and producer != release_key:
            phase = owners[("HelmRelease", *release_key)]
            producer_phase = owners[("HelmRelease", *producer)]
            assert producer in release_ancestors(release_key) or producer_phase in ancestors(phase), (
                f"{release_key} can install {doc['kind']} before {producer} provides its CRD"
            )

    services = {identity(doc)[1:]: doc for doc in rendered if doc["kind"] == "Service"}
    for doc in objects:
        if doc["kind"] == "HTTPRoute":
            for rule in doc["spec"]["rules"]:
                for backend in rule.get("backendRefs", []):
                    service = services[(doc["metadata"]["namespace"], backend["name"])]
                    assert backend["port"] in [port["port"] for port in service["spec"]["ports"]]
    print(f"Validated {len(phases)} Flux phases, {len(releases)} charts, CRD ordering and HTTP routing.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, help="Reuse previously downloaded pinned charts and Flux schemas")
    args = parser.parse_args()
    if args.cache:
        validate(args.cache)
    else:
        with tempfile.TemporaryDirectory(prefix="gitops-validate-") as directory:
            validate(Path(directory))
