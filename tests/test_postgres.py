import copy
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.cluster import Cluster
from scripts.postgres import Postgres, bootstrap


MARKER = {"version": 1, "postgres_major": 18, "server_name": "platform-" + "a" * 32,
          "backup_id": "20261010T200000", "target_name": "shutdown_" + "b" * 32}
CONNECTION = {"storage_account": "testbackups", "container": "postgres-backups",
              "subscription_id": "subscription", "tenant_id": "tenant", "client_id": "client"}


class PostgresTests(unittest.TestCase):
    def setUp(self):
        self.cluster = Mock()
        self.cluster.tf = ["terraform"]
        self.cluster.kube = ["kubectl"]
        self.cluster.connection = {"subscription_id": "subscription", "resource_group": "rg", "name": "aks"}
        self.run = Mock(return_value=json.dumps(CONNECTION))
        self.wait = lambda check, description, timeout: self.assertTrue(check(), description)
        self.pg = Postgres(self.cluster, self.run, self.wait)

    def test_first_install_and_restore_use_distinct_bootstrap_methods(self):
        self.assertEqual(bootstrap(None), {"initdb": {"database": "app", "owner": "app"}})
        recovered = bootstrap(MARKER)
        self.assertNotIn("initdb", recovered)
        self.assertEqual(recovered["recovery"]["recoveryTarget"],
                         {"backupID": MARKER["backup_id"], "targetName": MARKER["target_name"]})

    def test_corrupt_or_other_major_marker_never_initializes_empty_database(self):
        for marker in [[], {}, MARKER | {"postgres_major": 17}, MARKER | {"backup_id": "unknown"},
                       MARKER | {"target_name": "invalid' SQL"}, MARKER | {"server_name": "../other"}]:
            with self.subTest(marker=marker), self.assertRaises(ValueError):
                bootstrap(marker)

    def test_missing_marker_with_existing_archive_blocks_initialization(self):
        self.pg.exists = Mock(return_value=False)
        self.run.return_value = '["archive/old/base/backup.info"]'
        with self.assertRaisesRegex(RuntimeError, "without a recovery marker"):
            self.pg.read_marker()

    def test_marker_referencing_missing_backup_blocks_initialization(self):
        self.pg.exists = Mock(side_effect=[True, False])

        def download(*args, **kwargs):
            Path(args[args.index("--file") + 1]).write_text(json.dumps(MARKER))

        self.run.side_effect = download
        with self.assertRaisesRegex(RuntimeError, "missing base backup"):
            self.pg.read_marker()

    def test_recreation_recovers_previous_archive_and_writes_to_new_archive(self):
        self.cluster.objects.return_value = []
        self.pg.read_marker = Mock(return_value=MARKER)
        self.pg.manifest = Mock()
        self.assertTrue(self.pg.prepare())
        data = self.pg.manifest.call_args.args[0]["data"]
        self.assertEqual(data["POSTGRES_RECOVERY_SERVER"], MARKER["server_name"])
        self.assertNotEqual(data["POSTGRES_ARCHIVE_SERVER"], MARKER["server_name"])
        self.assertEqual(json.loads(data["POSTGRES_BOOTSTRAP"]), bootstrap(MARKER))

    def test_repeated_up_preserves_existing_bootstrap_and_archive(self):
        data = {"POSTGRES_ARCHIVE_SERVER": "platform-" + "c" * 32,
                "POSTGRES_RECOVERY_SERVER": MARKER["server_name"],
                "POSTGRES_BOOTSTRAP": json.dumps(bootstrap(MARKER))}
        self.cluster.objects.return_value = [{"metadata": {"name": "postgres-settings"}, "data": copy.copy(data)}]
        self.pg.read_marker = Mock()
        self.pg.manifest = Mock()
        self.assertFalse(self.pg.prepare())
        result = self.pg.manifest.call_args.args[0]["data"]
        self.assertEqual({key: result[key] for key in data}, data)
        self.pg.read_marker.assert_not_called()

    def test_network_policy_engine_is_required(self):
        self.run.return_value = json.dumps({"version": "1.34.3", "oidc": True, "workload": True, "policy": None})
        with self.assertRaisesRegex(RuntimeError, "network policy"):
            self.pg.preflight()

    def configure_backup(self, phase="completed"):
        self.pg.database = Mock(return_value={"metadata": {"name": "platform"}, "spec": {"plugins": [
            {"name": "barman-cloud.cloudnative-pg.io", "parameters": {"serverName": MARKER["server_name"]}}]}})
        docs = []

        def create(doc):
            if doc["kind"] == "Backup":
                docs.append(doc | {"status": {"phase": phase, "backupId": MARKER["backup_id"]}})

        self.pg.manifest = Mock(side_effect=create)
        self.cluster.objects.side_effect = lambda kind, ns: docs if kind == "backups.postgresql.cnpg.io" else []
        self.pg.sql = Mock(side_effect=lambda sql: "A" * 24 if "pg_walfile_name" in sql else "t")
        self.pg.exists = Mock(return_value=True)

    def test_failed_backup_does_not_publish_recovery_marker(self):
        self.configure_backup("failed")
        self.run.reset_mock()
        with self.assertRaisesRegex(RuntimeError, "backup .* failed"):
            self.pg.backup(shutdown=True)
        self.assertFalse(any("upload" in call.args for call in self.run.call_args_list))

    def test_shutdown_waits_for_wal_and_verifies_marker(self):
        self.configure_backup()
        uploaded = {}

        def upload(*args, **kwargs):
            if "upload" in args:
                uploaded.update(json.loads(Path(args[args.index("--file") + 1]).read_text()))

        self.run.side_effect = upload
        self.pg.read_marker = Mock(side_effect=lambda: copy.copy(uploaded))
        self.pg.backup(shutdown=True)
        self.assertEqual(uploaded["backup_id"], MARKER["backup_id"])
        self.assertTrue(uploaded["target_name"].startswith("shutdown_"))
        queries = [call.args[0] for call in self.pg.sql.call_args_list]
        self.assertTrue(any("pg_stat_archiver" in query for query in queries))
        self.assertTrue(any("pg_terminate_backend" in query for query in queries))
        self.pg.read_marker.assert_called_once()

    def test_failed_wal_archive_does_not_publish_recovery_marker(self):
        self.configure_backup()
        self.pg.sql.side_effect = lambda sql: "f" if "pg_stat_archiver" in sql else ("A" * 24 if "pg_walfile_name" in sql else "t")
        self.run.reset_mock()
        with self.assertRaises(AssertionError):
            self.pg.backup(shutdown=True)
        self.assertFalse(any("upload" in call.args for call in self.run.call_args_list))

    def test_cleanup_keeps_database_and_controllers_when_backup_fails(self):
        cluster = Cluster("/unused")
        cluster.kube = ["kubectl"]
        cluster.objects = Mock(side_effect=lambda kind, ns=None:
                               [{"metadata": {"name": "platform"}}] if kind == "clusters.postgresql.cnpg.io" else [])
        cluster.delete = Mock()
        with patch("scripts.cluster.run", return_value="clusters.postgresql.cnpg.io"), \
                patch("scripts.cluster.Postgres") as postgres:
            postgres.return_value.backup.side_effect = RuntimeError("Backup failed")
            with self.assertRaises(RuntimeError):
                cluster.cleanup()
        self.assertFalse(any(call.args[0] == "clusters.postgresql.cnpg.io" for call in cluster.delete.call_args_list))
        self.assertFalse(any("cnpg-system" in call.args for call in cluster.delete.call_args_list))

    def test_cleanup_removes_database_before_backup_plugin_and_operator(self):
        cluster = Cluster("/unused")
        cluster.kube = ["kubectl"]
        cluster.objects = Mock(side_effect=lambda kind, ns=None:
                               [{"metadata": {"name": "platform"}}] if kind == "clusters.postgresql.cnpg.io" else [])
        cluster.delete = Mock()
        with patch("scripts.cluster.run", return_value="clusters.postgresql.cnpg.io"), \
                patch("scripts.cluster.Postgres"):
            cluster.cleanup()
        calls = cluster.delete.call_args_list
        database = next(i for i, call in enumerate(calls) if call.args[0] == "clusters.postgresql.cnpg.io")
        plugin = next(i for i, call in enumerate(calls) if call.args[:2] == ("helmreleases", ["plugin-barman-cloud"]))
        operator = next(i for i, call in enumerate(calls) if call.args[:2] == ("helmreleases", ["cloudnative-pg"]))
        certificates = next(i for i, call in enumerate(calls) if call.args[:2] == ("helmreleases", ["cert-manager"]))
        self.assertLess(database, plugin)
        self.assertLess(plugin, operator)
        self.assertLess(operator, certificates)


if __name__ == "__main__":
    unittest.main()
