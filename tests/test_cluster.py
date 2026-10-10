import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.cluster import Cluster, main


class LifecycleTests(unittest.TestCase):
    def invoke_down(self, cluster):
        cluster.tf = ["terraform"]
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "main.tf").touch()
            with patch.object(sys, "argv", ["cluster.py", "down", "--infra-dir", directory]), \
                    patch("scripts.cluster.shutil.which", return_value="tool"), \
                    patch("scripts.cluster.run"), \
                    patch("scripts.cluster.Cluster", return_value=cluster):
                main()

    def test_cleanup_failure_prevents_terraform_deletion(self):
        from unittest.mock import Mock
        cluster = Mock()
        cluster.connect.return_value = True
        cluster.cleanup.side_effect = TimeoutError("database finalizer still running")
        with self.assertRaises(TimeoutError):
            self.invoke_down(cluster)
        cluster.apply.assert_not_called()

    def test_repeated_down_never_recreates_absent_cluster(self):
        from unittest.mock import Mock
        cluster = Mock()
        cluster.connect.return_value = False
        self.invoke_down(cluster)
        cluster.cleanup.assert_not_called()
        cluster.apply.assert_called_once_with(False, False)

    def test_readiness_waits_for_crds_and_current_generation(self):
        cluster = Cluster("/unused")
        cluster.kube = ["kubectl"]
        attempts = 0
        api_checks = 0

        def command(*args, **kwargs):
            nonlocal api_checks
            if "api-resources" in args:
                api_checks += 1
                return "" if api_checks == 1 else "kustomizations.example\nhelmreleases.example"

        def objects(kind, namespace=None):
            size = 9 if kind == "kustomizations" else 12
            return [{"metadata": {"name": "flux-system" if i == 0 else str(i), "generation": 2},
                     "spec": {}, "status": {"conditions": [{"type": "Ready", "status": "True",
                     "observedGeneration": 2 if attempts >= 4 else 1}]}} for i in range(size)]

        def wait(check, description, timeout):
            nonlocal attempts
            for attempts in range(1, 6):
                if check():
                    self.assertGreaterEqual(attempts, 4)
                    return
            self.fail("Readiness never converged")

        with patch("scripts.cluster.run", side_effect=command), \
                patch.object(cluster, "objects", side_effect=objects), \
                patch("scripts.cluster.wait_for", side_effect=wait):
            cluster.ready()


if __name__ == "__main__":
    unittest.main()
