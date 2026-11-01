# Manual application registration

This directory registers one disposable application on management Argo. Platform
bootstrap does not load it. `application.yaml` has manual sync and no cascade
finalizer until ordered SQL cleanup is proved; merging these files deploys nothing.

The sources have separate responsibilities:

- The merged application chart supplies reusable Kubernetes resources.
- The pinned application repository supplies `deploy/values.yaml`, including its
  components, selected images/migration and database requests. The hostname is an
  environment binding here. Updating that values pin requires GitOps review.
- This GitOps directory owns trusted ESO credential/RBAC policy. Argo reads only
  `credentials.yaml`, rather than registering the Application recursively.

Argo combines the chart and credential manifests into one wave sequence:
namespace -> generated passwords/reader access -> copied credentials -> Atlas
roles -> CNPG database -> access migration -> application migration -> workloads.
The API receives runtime credentials only, with verified direct PostgreSQL TLS,
database-aware readiness and independent liveness. nginx proxies `/api` to `api:8000`.
Both image digests and the migration tag/checksum select the verified artifact set
from [main run41315554435](https://github.com/ai-platform-portfolio/preview-environments/actions/runs/41315554435),
source `a429264`. The application values commit is deployment configuration, not a
new image build selection. PR automation and artifact revision updates come later.

ESO keeps two source passwords immutable in `postgres`. Atlas gets scoped copies;
a separate named reader copies only the runtime password/public CA to the app
namespace. Migrator and role-manager credentials never reach application pods.
The CA projection contains only `ca.crt`; only the API receives DATABASE_URL.

Before registering or syncing:

1. Merge the app-owned values PR and update its pin to the actual merged revision.
2. Apply the already-merged bootstrap Argo ConfigMap through its existing owner;
   current-generation Atlas health checks are still absent live.
3. Add the following entry to the shared platform's `postgres.additionalHba` in
   `helm_chart/platform/values.yaml` through a reviewed GitOps change, then reconcile
   PostgreSQL before this application. The extension is empty by default:

   ```yaml
   postgres:
     additionalHba:
       - hostssl preview_manual preview_manual_migrator,preview_manual_app all scram-sha-256
   ```

4. Verify PostgreSQL/Atlas/ESO, the existing `atlas-postgres` SecretStore, named
   reader access, CA, bot entitlement and the `preview-roles` schema plan repository.
   The Atlas listing currently exposes only a migration-directory repository;
   schema-plan storage remains unverified.
5. Obtain deployment approval, register this Application on the management cluster
   and explicitly sync it. Prove HTTPS save/read, SQL restrictions and readiness.

`make validate` renders the actual pinned chart and external app values, checks
combined resource ordering, ownership and credential boundaries, and validates
that additional HBA rules precede the shared reject-all rule. Rendering is not
live SQL/operator acceptance.

Keep names and roleVars stable; access changes append forward SQL migrations.
Do not delete the Application as teardown: stop workloads/migrations, drop this
DB and wait, remove its Atlas-owned roles while their scope/credentials remain,
then remove Kubernetes resources/source passwords. Removing Atlas CRs alone leaves
SQL roles. Repeatable ordered cleanup remains the next lifecycle task.

Sources: [external Helm values](https://argo-cd.readthedocs.io/en/stable/user-guide/multiple_sources/#helm-value-files-from-external-git-repository),
[Atlas repository types](https://atlasgo.io/cloud/features/registry).
