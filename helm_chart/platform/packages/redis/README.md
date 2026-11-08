# Shared Redis Open Source

The platform installs Redis **8.10.3**, the latest stable upstream release checked
on 2026-11-08, using the official Alpine image pinned by its multi-platform digest.
Redis 8 is available under AGPLv3 as well as RSALv2/SSPLv1; this package uses the
open-source distribution. It needs no PostgreSQL or Azure service permissions.
Argo keeps its separate bootstrap-owned Redis.

`templates/redis.yaml` installs a namespace-scoped Reloader at wave 5, certificates
at wave 20 (after cert-manager and Reflector), then Redis at wave 30. Langfuse
starts at wave 40. Reverse waves remove clients/server before certificates and
controllers. The existing resource chart renders the small native StatefulSet;
there is no new operator, CRD, password generator or bootstrap handoff.

## Configuration and access

```yaml
redis:
  enabled: true

langfuse:
  redis:
    useShared: true
```

Defaults are one instance, 100m CPU/512Mi requested memory, a 1Gi memory limit,
256Mi Redis memory ceiling, `noeviction`, AOF with `appendfsync everysec`, and an
8Gi `managed-csi` volume on the application node pool. Change sizing in platform
values according to measured queue/backlog usage. AOF may lose approximately one
second of acknowledged writes on a crash; there is no HA or off-cluster backup.
The pod has no API token, capabilities or writable root filesystem. The disk is
writable through its filesystem group, with no privileged chown init container.
PVCs survive StatefulSet replacement; deleting the package namespace/cluster
removes disposable data. Disabling this package prunes its namespace: it is not
a data-preserving pause. Retained disks still consume capacity until deletion.

Only `redis.redis.svc.cluster.local:6379` is exposed, using a ClusterIP Service
and mandatory mutual TLS. Plaintext port 0 is disabled. No NetworkPolicies,
Gateway routes, load balancers or new private networking are added. ClusterIP
alone does not restrict which pods can attempt connections; mTLS and ACLs enforce
client access. Redis has no native Entra/WIF token validation in this setup;
adding a federated Azure identity would grant unrelated permissions.

Redis maps the verified certificate CN to an enabled ACL user. The disabled
`default` user rejects anonymous access, and `resetpass` without `nopass` prevents
password AUTH from selecting a different identity. The probe may only PING.
Langfuse can operate on `langfuse:*` keys/channels, with data/queue/script commands
but no administrative or destructive whole-database commands. Lua scripts remain
subject to ACL checks. INFO and SCAN expose some server/key metadata; this is a
shared trusted platform store, not a hostile-tenant isolation boundary. Adding
another application requires its own reviewed identity/ACL integration in the
shared application chart, never shared client credentials or per-app GitOps lists.

## Certificate lifecycle

cert-manager creates a dedicated CA in `redis`. Server, probe and Langfuse leaves
last 30 days, renew seven days before expiry and generate fresh private keys.
Only the Langfuse leaf Secret (including its public CA certificate) is reflected
to the exact `^langfuse$` namespace. No server, probe or CA signing key is copied.
The existing Langfuse Reloader restarts web/worker after the delivered Secret
changes; the Redis-scoped Reloader restarts Redis after its mounted certificates
or configuration change. Argo ignores only each reloader's rollout annotation.
Probes connect locally with TLS to avoid depending on a Service endpoint becoming
Ready before the pod itself; Langfuse verifies the Service hostname and CA.

Single-instance restarts interrupt service, and clients must reconnect. Renewal
scheduling, Secret reflection and reload are configured; live renewal acceptance
must verify the new serial/key in both client pods and continued queue processing.
Existing TLS connections are not immediately revoked when a certificate changes.
The dedicated ten-year CA key is a longer-lived trust anchor, kept only in its
namespace. cert-manager CA issuance does not automate safe CA trust rollover or
revocation. Plan overlapping trust and leaf replacement before CA expiry or after
compromise; never copy the PostgreSQL CA key or claim leaf renewal solves this.

## Langfuse connection and validation

`langfuse.redis.useShared: true` disables bundled Valkey, selects the shared
Service and native CA/client-key paths, adds `REDIS_KEY_PREFIX=langfuse:`, and
updates both clients' reload annotations. No password enters the new URL.
The user confirmed this Langfuse installation is unused and authorized direct
cutover; no queue/data migration or old-store retention is required.

After deployment, verify Redis and Langfuse Synced/Healthy, the reflected client
certificate issuer/CN, web/worker TLS connectivity and enqueue/process a uniquely
identified observation. Verify telemetry ingestion and renewal/reconnection
without logging private keys. Configured certificate scheduling, Secret reflection
and reload do not prove a live renewal until exercised together.

A later switch back to bundled Valkey selects a different store; it does not
transfer pending jobs. Any future installation holding real queue data must
review its data handling before changing endpoints. The existing bootstrap helper
still prepares the legacy credential for bundled-store compatibility; shared
Redis neither accepts nor consumes it. No new static Redis password is introduced.

Sources: [Redis release](https://github.com/redis/redis/releases/tag/8.10.3),
[Redis configuration](https://github.com/redis/redis/blob/8.10.3/redis.conf),
[Langfuse cache configuration](https://langfuse.com/self-hosting/deployment/infrastructure/cache),
[CA issuer limitations](https://cert-manager.io/docs/configuration/ca/),
[Reflector namespace restrictions](https://github.com/emberstack/kubernetes-reflector#usage).
