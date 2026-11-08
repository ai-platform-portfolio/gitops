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
    useShared: false # Prepare first; enable in the separately reviewed cutover.
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

## Langfuse migration and rollback

Installation/retention preparation and connection cutover are separate PRs.
**Do not merge the cutover until the preparation has reconciled.** Check:

1. Redis, its certificates and Reloader are Synced/Healthy. The reflected
   `langfuse/langfuse-redis` Secret exists with the expected client CN/issuer;
   inspect certificate metadata without logging private keys.
2. `langfuse/langfuse-redis` **PVC** has
   `argocd.argoproj.io/sync-options: Prune=false,Delete=false` and
   `helm.sh/resource-policy: keep`. The preparation applies these through the
   bundled chart's native `dataStorage` values; rendering them is not live proof.
3. Stop ingestion producers, pause scheduled producers and let workers drain.
   Using the installed BullMQ API, verify zero waiting, active, delayed,
   prioritized and waiting-children work in every queue, including paused queues
   and retry/dead-letter paths. Account for repeatable jobs/job schedulers and
   any failed jobs to retain/retry. Do not flush queues, infer emptiness from pod
   health, or copy a Valkey RDB into a different Redis version without validation.
4. Merge the cutover (`useShared: true`). It disables bundled Valkey, selects the
   shared Service and native CA/client-key paths, adds `REDIS_KEY_PREFIX=langfuse:`,
   and updates both clients' reload annotations. No password enters the new URL.
5. Verify web/worker readiness, enqueue/process a uniquely identified observation,
   find it through Langfuse, confirm telemetry ingestion, test reconnection and
   fresh-key renewal, then resume producers. Queue counts/rotation are live gates;
   local container tests and CI renders do not prove them.

The old PVC and existing password Secret remain for rollback. Reverting only the
connection flag starts the bundled store again; stop producers and drain the
**new** queue first to avoid stranding work or replaying old pending jobs. Retire
the old PVC/credential only after successful acceptance in an explicitly reviewed
cleanup. The PVC retention annotations also retain it on application deletion;
normal namespace/cluster teardown still removes it. Retention is not a backup.

Sources: [Redis release](https://github.com/redis/redis/releases/tag/8.10.3),
[Redis configuration](https://github.com/redis/redis/blob/8.10.3/redis.conf),
[Langfuse cache configuration](https://langfuse.com/self-hosting/deployment/infrastructure/cache),
[CA issuer limitations](https://cert-manager.io/docs/configuration/ca/),
[Reflector namespace restrictions](https://github.com/emberstack/kubernetes-reflector#usage).
