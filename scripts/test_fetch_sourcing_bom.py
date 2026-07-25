import unittest
import io
import os
import sys
from unittest.mock import patch, mock_open

# Ensure scripts directory is in path if we are running from root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_sourcing_bom

class TestFetchSourcingBom(unittest.TestCase):

    @patch('fetch_sourcing_bom.DIGIKEY_CLIENT_ID', 'dummy_client_id')
    @patch('fetch_sourcing_bom.DIGIKEY_CLIENT_SECRET', 'dummy_client_secret')
    @patch('fetch_sourcing_bom.DIGIKEY_TOKEN_PATH', 'dummy_token.json')
    @patch('os.path.exists')
    def test_get_digikey_access_token_json_error(self, mock_exists):
        # Setup: Token path exists but contains invalid JSON
        mock_exists.return_value = True
        invalid_json_data = "{invalid_json: true"

        # Capture stderr to check for the warning message
        captured_stderr = io.StringIO()

        # Mock open to return invalid json, and os.getenv to avoid reading environment variables
        with patch('builtins.open', mock_open(read_data=invalid_json_data)):
            with patch('sys.stderr', captured_stderr):
                with patch('os.getenv', return_value=None):
                    # We expect it to return None because it will fail to load the file,
                    # and won't have a refresh token or access token from environment.
                    result = fetch_sourcing_bom.get_digikey_access_token()

        self.assertIsNone(result)
        self.assertIn("Warning: Failed to load DigiKey token file", captured_stderr.getvalue())

if __name__ == '__main__':
    unittest.main()
