"""
Basic usage example for opensandbox-azure.

Run with:
    pip install opensandbox-azure
    python examples/basic_usage.py
"""

from opensandbox_azure import SandboxClient
from azure.identity import DefaultAzureCredential

client = SandboxClient(
    api_url="https://api-opensandbox.example.com",
    credential=DefaultAzureCredential(),
    scope="api://<api-app-id>/.default",  # replace <api-app-id> with your App Registration client ID
)

sess = client.create_session(image="acr.example.azurecr.io/sandbox/base/python:3.12")
try:
    result = sess.run("python -c 'print(\"hello\")'")
    print(result.stdout)
    print(f"exit_code={result.exit_code}  duration_ms={result.duration_ms}")
finally:
    sess.delete()
