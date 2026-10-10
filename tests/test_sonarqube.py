import unittest
from unittest.mock import Mock

from scripts.sonarqube import SonarQube, validate_credentials


class SonarCredentialsTests(unittest.TestCase):
    def test_existing_database_without_credentials_fails_closed(self):
        postgres = Mock()
        postgres.exists.return_value = False
        run = Mock()
        sonar = SonarQube(Mock(), postgres, run, Mock())
        with self.assertRaisesRegex(RuntimeError, 'without retained credentials'):
            sonar.credentials(database_exists=True)
        run.assert_not_called()

    def test_credentials_reject_sql_metacharacters(self):
        record = {'version': 1, 'jdbc-password': "'" * 43,
                  'admin-password': 'a' * 43, 'monitoring-passcode': 'b' * 43}
        with self.assertRaises(ValueError):
            validate_credentials(record)

    def test_credentials_reject_unknown_version(self):
        with self.assertRaises(ValueError):
            validate_credentials({'version': 2})
