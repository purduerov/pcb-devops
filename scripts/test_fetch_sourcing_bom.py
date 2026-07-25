import unittest
import os
import tempfile
import json
import stat
from unittest.mock import patch, MagicMock

# Import the module to test
import fetch_sourcing_bom

class TestFetchSourcingBomSecurity(unittest.TestCase):
    def setUp(self):
        # Create a temporary file for the token
        self.temp_fd, self.temp_path = tempfile.mkstemp()
        os.close(self.temp_fd)

        # Ensure it has some open permissions initially to test chmod
        os.chmod(self.temp_path, 0o644)

        # Mock environment variables
        self.env_patcher = patch.dict(os.environ, {
            'DIGIKEY_CLIENT_ID': 'test_id',
            'DIGIKEY_CLIENT_SECRET': 'test_secret',
            'DIGIKEY_REFRESH_TOKEN': 'test_refresh',
            'DIGIKEY_TOKEN_PATH': self.temp_path
        })
        self.env_patcher.start()

        # Override the variable directly since it might be initialized at import time
        fetch_sourcing_bom.DIGIKEY_CLIENT_ID = 'test_id'
        fetch_sourcing_bom.DIGIKEY_CLIENT_SECRET = 'test_secret'
        fetch_sourcing_bom.DIGIKEY_TOKEN_PATH = self.temp_path

    def tearDown(self):
        self.env_patcher.stop()
        if os.path.exists(self.temp_path):
            os.remove(self.temp_path)

    @patch('urllib.request.urlopen')
    def test_digikey_token_file_permissions(self, mock_urlopen):
        """Test that the DigiKey token file is saved with secure permissions (0o600)."""
        # Mock the API response
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "access_token": "new_access",
            "refresh_token": "new_refresh"
        }).encode('utf-8')
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        # Trigger the token refresh and file save
        access_token = fetch_sourcing_bom.get_digikey_access_token()

        self.assertEqual(access_token, "new_access")

        # Verify the file was written to
        with open(self.temp_path, 'r') as f:
            data = json.load(f)
            self.assertEqual(data["access_token"], "new_access")

        # Check permissions
        file_stat = os.stat(self.temp_path)
        permissions = stat.S_IMODE(file_stat.st_mode)

        # Verify permissions are exactly 0o600
        self.assertEqual(permissions, 0o600, f"Expected permissions 0o600, got {oct(permissions)}")

if __name__ == '__main__':
    unittest.main()
