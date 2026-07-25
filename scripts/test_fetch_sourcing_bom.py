import os
import sys
import pytest
import fetch_sourcing_bom

def test_get_digikey_access_token_bad_json(monkeypatch, tmp_path, capsys):
    # Set client ID and secret to bypass the initial check
    monkeypatch.setattr(fetch_sourcing_bom, "DIGIKEY_CLIENT_ID", "dummy_client_id")
    monkeypatch.setattr(fetch_sourcing_bom, "DIGIKEY_CLIENT_SECRET", "dummy_client_secret")

    # Create a temporary file with invalid JSON
    bad_json_file = tmp_path / "bad_token.json"
    bad_json_file.write_text("{ this is not valid json ")

    # Point the script to use this bad JSON file
    monkeypatch.setattr(fetch_sourcing_bom, "DIGIKEY_TOKEN_PATH", str(bad_json_file))

    # Call the function
    token = fetch_sourcing_bom.get_digikey_access_token()

    # It should return None because the JSON fails to load and there's no env fallback (assuming fresh env)
    assert token is None

    # Check that the warning was printed to stderr
    captured = capsys.readouterr()
    assert "Warning: Failed to load DigiKey token file" in captured.err
