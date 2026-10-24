import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from helm_chart.scripts.sso import Azure, configure, prepare


class SsoTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 24, tzinfo=timezone.utc)
        self.settings = {'applicationName': 'portfolio-test-langfuse',
                         'redirectUri': 'https://langfuse.example.test/api/auth/callback/azure-ad',
                         'users': ['admin@example.test'], 'credentialValidityDays': 180,
                         'vaultSecretName': 'test-langfuse-oidc'}
        self.app = {'id': 'app-object', 'appId': 'app-client',
                    'tags': ['platform-gitops:portfolio-test-langfuse']}
        self.azure = Mock()
        self.azure.owner.return_value = 'owner'
        self.azure.collection.side_effect = self.collection
        self.azure.graph.side_effect = self.graph
        self.azure.secret.return_value = None

    def collection(self, path):
        if path.startswith('applications?'):
            return [self.app]
        if path.startswith('servicePrincipals?'):
            return [{'id': 'principal'}]
        if path.startswith('users?'):
            return [{'id': 'user', 'mail': 'admin@example.test'}]
        if path.endswith('/appRoleAssignedTo'):
            return [{'id': 'assignment', 'principalId': 'user'}]
        raise AssertionError(path)

    def graph(self, method, path, body=None):
        if method == 'GET':
            return {'passwordCredentials': [{'keyId': 'saved-key'}]}
        if path.endswith('/addPassword'):
            return {'secretText': 'private-password', 'keyId': 'new-key'}
        return None

    def test_valid_existing_credentials_are_not_rotated(self):
        saved = {'AUTH_AZURE_AD_CLIENT_ID': 'app-client', 'credentialKeyId': 'saved-key',
                 'expiresAt': (self.now + timedelta(days=90)).isoformat()}
        self.azure.secret.return_value = saved
        value, rotated = configure(self.azure, self.settings, 'tenant', 'vault', self.now)
        self.assertEqual(value, saved)
        self.assertFalse(rotated)
        self.azure.save_secret.assert_not_called()
        self.assertFalse(any(c.args[1].endswith('/addPassword') for c in self.azure.graph.call_args_list))

    def test_name_collision_is_not_adopted(self):
        self.app['tags'] = []
        with self.assertRaisesRegex(RuntimeError, 'ownership'):
            configure(self.azure, self.settings, 'tenant', 'vault', self.now)
        self.azure.owner.assert_not_called()
        self.azure.graph.assert_not_called()

    def test_kv_failure_removes_only_the_unpersisted_new_credential(self):
        self.azure.save_secret.side_effect = RuntimeError('write failed')
        with self.assertRaisesRegex(RuntimeError, 'write failed'):
            configure(self.azure, self.settings, 'tenant', 'vault', self.now)
        self.azure.graph.assert_any_call('POST', 'applications/app-object/removePassword', {'keyId': 'new-key'})

    def test_old_application_credentials_are_not_overwritten(self):
        self.azure.secret.return_value = {'AUTH_AZURE_AD_CLIENT_ID': 'other-app'}
        with self.assertRaisesRegex(RuntimeError, 'refusing to overwrite'):
            configure(self.azure, self.settings, 'tenant', 'vault', self.now)
        self.azure.save_secret.assert_not_called()

    def test_only_explicit_users_keep_assignments(self):
        original = self.collection
        self.azure.collection.side_effect = lambda path: (
            [{'id': 'old-assignment', 'principalId': 'removed-user'}]
            if path.endswith('/appRoleAssignedTo') else original(path))
        configure(self.azure, self.settings, 'tenant', 'vault', self.now)
        self.azure.graph.assert_any_call('POST', 'servicePrincipals/principal/appRoleAssignedTo', {
            'principalId': 'user', 'resourceId': 'principal',
            'appRoleId': '00000000-0000-0000-0000-000000000000'})
        self.azure.graph.assert_any_call('DELETE', 'servicePrincipals/principal/appRoleAssignedTo/old-assignment')

    def test_stale_gitops_settings_cannot_start_identity_writes(self):
        cluster = Mock()
        cluster.settings = {'git': {'revision': 'a' * 40}}
        cluster.object.return_value = {}
        def wait(check, description):
            self.assertFalse(check())
            raise TimeoutError('still waiting')
        with patch('helm_chart.scripts.sso.Azure') as azure:
            with self.assertRaises(TimeoutError):
                prepare(cluster, wait)
            azure.assert_not_called()

    def test_cli_errors_do_not_expose_response_bodies(self):
        result = SimpleNamespace(returncode=1, stderr='private-password and tenant-id', stdout='private-token')
        with patch('helm_chart.scripts.sso.subprocess.run', return_value=result):
            with self.assertRaises(RuntimeError) as error:
                Azure().call(['rest'])
        self.assertNotIn('private', str(error.exception))
        self.assertNotIn('tenant-id', str(error.exception))

    def test_secret_is_written_via_private_file_not_command_arguments(self):
        azure = Azure()
        observed = []
        def call(args):
            filename = Path(args[args.index('--file') + 1])
            self.assertEqual(filename.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(filename.read_text()), {'secret': 'private-password'})
            self.assertNotIn('private-password', str(args))
            observed.append(filename)
        azure.call = call
        azure.save_secret('vault', 'name', {'secret': 'private-password'})
        self.assertFalse(observed[0].exists())


if __name__ == '__main__':
    unittest.main()
