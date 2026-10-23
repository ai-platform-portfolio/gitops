# ${{ values.name }}

Owner: ${{ values.owner }}. Lifecycle: experimental.

Run `python -m unittest` to exercise the health and error routes.
Run `python app.py` and request `/healthz` on port 8080. Build the container,
scan it, replace the deployment placeholder with its immutable image digest,
and attach the reviewed manifests to the destination cluster's GitOps root.
The template opens a PR; it does not merge, build an image or deploy a service.

During an incident, check rollout events, pod restarts and the last approved
revision. Use the platform runbook for rollback. Do not put customer data in logs.
