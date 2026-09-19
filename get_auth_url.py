#!/usr/bin/env python3
"""Generate auth URL and save the code verifier for later use."""
import json
import sys
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

BASE_DIR = Path.home() / 'blogger-auto-poster'
CREDENTIALS_PATH = BASE_DIR / 'client_secret.json'
SCOPES = ['https://www.googleapis.com/auth/blogger']

if not CREDENTIALS_PATH.exists():
    print(f"Error: {CREDENTIALS_PATH} not found")
    sys.exit(1)

flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
flow.redirect_uri = 'http://localhost:8080'

auth_url, state = flow.authorization_url(prompt='consent', access_type='offline')

# Save the code verifier and state for later
verifier_data = {
    'code_verifier': flow.code_verifier,
    'state': state
}

with open(BASE_DIR / 'verifier.json', 'w') as f:
    json.dump(verifier_data, f)

print(f"Auth URL:\n\n{auth_url}\n")
print(f"\nVerifier saved to: {BASE_DIR / 'verifier.json'}")
print("Now paste your auth code to auth_code.txt and run: python3 complete_auth.py")
