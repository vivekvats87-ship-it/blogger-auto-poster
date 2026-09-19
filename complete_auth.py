#!/usr/bin/env python3
"""Complete OAuth using saved code verifier, then run the auto-poster pipeline."""
import json
import sys
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

BASE_DIR = Path.home() / 'blogger-auto-poster'
CREDENTIALS_PATH = BASE_DIR / 'client_secret.json'
TOKEN_PATH = BASE_DIR / 'token.json'
VERIFIER_PATH = BASE_DIR / 'verifier.json'
SCOPES = ['https://www.googleapis.com/auth/blogger']

def main():
    # Load saved verifier
    if not VERIFIER_PATH.exists():
        print("Error: verifier.json not found. Run get_auth_url.py first.")
        sys.exit(1)

    with open(VERIFIER_PATH, 'r') as f:
        verifier_data = json.load(f)

    code_verifier = verifier_data['code_verifier']

    # Read the auth code
    auth_code_file = BASE_DIR / 'auth_code.txt'
    if not auth_code_file.exists():
        print(f"Error: {auth_code_file} not found. Paste the code there first.")
        sys.exit(1)

    with open(auth_code_file, 'r') as f:
        code = f.read().strip()

    auth_code_file.unlink()

    # Create flow with saved verifier
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
    flow.redirect_uri = 'http://localhost:8080'
    flow.code_verifier = code_verifier

    # Exchange code for token
    flow.fetch_token(code=code)

    # Save token
    with open(TOKEN_PATH, 'w') as token:
        token.write(flow.credentials.to_json())

    print("OAuth token saved successfully!")

    # Clean up
    VERIFIER_PATH.unlink(missing_ok=True)

    # Now run the main pipeline
    print("\nRunning auto-poster pipeline...\n")
    from auto_poster import main as run_pipeline
    run_pipeline()

if __name__ == '__main__':
    main()
