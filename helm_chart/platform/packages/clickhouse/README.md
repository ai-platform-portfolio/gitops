# ClickHouse

[The Applications](../../templates/clickhouse.yaml) install the operator at wave 10,
after cert-manager at wave 5, and the database at wave 30. The operator chart
uses cert-manager to issue its webhook certificate. Keeping these Applications
separate allows operator readiness to gate creation of database custom resources.

`operator-values.yaml` configures the operator; `values.yaml` configures one
ClickHouse replica and one Keeper replica. Namespace creation happens in the
shared `namespaces` stage.

This standalone release is separate from Langfuse's bundled ClickHouse. Its
cluster-local data is disposable and is not covered by the CNPG backup schedule.
