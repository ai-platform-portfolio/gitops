# Backstage application

Self-hosted Backstage 1.56.2 provides the catalog, relationships, API documentation,
TechDocs, PostgreSQL-backed search and a reviewed service-request template. The
new frontend/backend systems are used. Guest login, the example allow-all policy,
MCP, cluster access, notification integrations and generic credentialed proxies
are not installed. This application is deployed by the
[Backstage platform package](../platform/packages/backstage/README.md).

Source and deployment live in separate directories of this repository so one PR
can review the application contract and its consumers. A future application repo
can preserve the image/config interface without changing deployment ownership.

## Build and update

Use Node 24.22.1, Corepack and the Yarn version in `packageManager`. Installation
uses `yarn.lock`; CI never regenerates it. From this directory:

```sh
corepack enable
yarn install --immutable
yarn tsc --noEmit
yarn test --watch=false --maxWorkers=2
node scripts/check-template.mjs
yarn lint:all
python -m pip install -r scripts/requirements.txt
bash scripts/build-docs.sh
yarn build:backend
docker build -t backstage:local -f packages/backend/Dockerfile .
```

Build configuration needs `BACKSTAGE_BASE_URL`, `AUTH_SESSION_SECRET`,
`AUTH_OIDC_METADATA_URL`, `AUTH_OIDC_CLIENT_ID`, `AUTH_OIDC_CLIENT_SECRET`,
`POSTGRES_PASSWORD` and `POSTGRES_CA`. Use synthetic values for public CI as shown
in its workflow; private runtime configuration must never enter public builds.
The backend injects the deployment's frontend-visible configuration at runtime.

The application workflow validates PRs and builds a container. Main pushes also
publish `ghcr.io/ai-platform-portfolio/gitops/backstage:<commit>`; promote its
immutable digest explicitly through private deployment inputs. Publication does
not deploy. For a company installation, use an approved internal runner/registry;
this public portfolio workflow must never build private catalog or documentation.

For Backstage upgrades, run `yarn backstage-cli versions:bump --release <version>`,
review Upgrade Helper and applicable codemods, then rerun CI and the live exercises.
Do not update Backstage packages independently of the coordinated release.
Review base images, Node, Python documentation dependencies and community
integrations separately. Yarn reports inherited Material UI/React peer range
warnings; compilation and tests check this generated upstream combination.

## Identity and permissions

The generic company OIDC provider is configured through Secrets. Register callback
`https://backstage.<domain>/api/auth/oidc/handler/frame`. Identity resolution uses
exact email matching against operator-maintained catalog users; there is no
allow-missing-user fallback. Use a trusted issuer and its verified company email
claims. OIDC client setup belongs to the organisation's identity administration.

The `catalog-org.yaml` Secret contains trusted User/Group membership. Readers may
read catalog entities and template descriptions. Platform-team members may create
tasks; all users, including operators, can only read/cancel their own tasks.
Only `fetch:template` and `publish:github:pull-request` are permitted; the publishing
condition requires the operator-configured target repository. Unknown permissions,
catalog mutation/import and arbitrary template registration are denied. This is
one shared internal visibility domain, not per-team confidentiality isolation.

`permission.ts` is the authorisation implementation and its tests cover anonymous
access, readers, operators, unknown permissions, task ownership and action limits.
The underlying repository App must independently restrict accessible repositories
and operations; portal permissions are not a substitute for upstream controls.

## Service request contract

`templates/service` generates a small Python HTTP service, health probes, resource
requests, a non-root container/deployment, ownership metadata and a runbook. It
requires a Linear issue and opens a uniquely named branch/PR in the configured
company repository. An existing service name/branch needs investigation; the
workflow does not blindly replace an existing service.

This is a reviewable service request, not a completed automatic delivery pipeline.
The generated image placeholder deliberately cannot deploy. Review/build/scan the
service image, pin its digest and attach its manifests to the destination root.
No new service auto-registration, environment provisioning, telemetry
instrumentation, production promotion or automated remediation is claimed.
These later steps need the company's service repo/CI/environment contracts, not
hard-coded guesses in this portal. Templates do not upgrade previous outputs.

The initial catalog models the developer platform, portal and database. Real
product systems/APIs and team ownership should be ingested from their authoritative
company repositories once that integration has been approved. Catalog descriptors
express declared relationships, not observed runtime dependencies.

## Documentation and data boundary

CI builds the portal's TechDocs with external fonts disabled. Static documentation
is bundled in the immutable image, served read-only and rebuilt with that image.
This avoids another storage service and runtime build workers. At organisation
scale move document publication to approved company-controlled object storage
with separate read/write identities, after measuring update frequency and image
size. The local publisher is intentionally an initial, bounded implementation.

There are no configured remote catalog URLs, hosted AI endpoints or SaaS analytics.
No private documentation/identity data is checked in. Mounted integration config
may add only company-approved endpoints; an empty `{}` keeps Git publishing
unconfigured. Network egress must also explicitly allow the approved endpoint IPs.
Do not put customer data or secrets into template parameters or task logs.
Future AI should consume scoped catalog/runbook context and propose validated
changes through bounded workers. This PR adds no AI inference or MCP tools.
