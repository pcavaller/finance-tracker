#!/usr/bin/env python3
import base64, json, os
from dotenv import load_dotenv
load_dotenv()

path = os.getenv('GOOGLE_CREDENTIALS_PATH', 'credentials.json')
with open(path) as f:
    data = json.load(f)
encoded = base64.b64encode(json.dumps(data).encode()).decode()
print("GOOGLE_CREDENTIALS_JSON=")
print(encoded)
