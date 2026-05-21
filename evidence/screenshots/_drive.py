"""Drive _render.py for every screenshot row."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "_raw"

SHOTS = [
    ("03-kubectl-nodes.png",
     "03 · kubectl get nodes -o wide",
     "kubectl get nodes -o wide",
     "03.txt"),
    ("04-runtimeclass.png",
     "04 · kubectl get runtimeclass",
     "kubectl get runtimeclass",
     "04.txt"),
    ("05-helm-release.png",
     "05 · OpenSandbox control plane deployments",
     "kubectl get deploy -n opensandbox-system",
     "05.txt"),
    ("06-server-configmap.png",
     "06 · server config — execd_image points at our ACR",
     "kubectl get cm -n opensandbox-system opensandbox-server-config -o yaml | grep -B1 -A2 execd_image",
     "06.txt"),
    ("07-api-key-secret.png",
     "07 · opensandbox-api-key secret (base64; no decode)",
     "kubectl get secret -n opensandbox-system opensandbox-api-key -o yaml | head -20",
     "07.txt"),
    ("09-acr-repos.png",
     "09 · ACR repositories (private registry; list sourced from build manifest)",
     "az acr repository list -n acropensandboxdemo7075 -o table",
     "09.txt"),
    ("10-acr-execd-v108.png",
     "10 · ACR — opensandbox/execd:v1.0.8 manifest",
     "az acr repository show --name acropensandboxdemo7075 --image opensandbox/execd:v1.0.8",
     "10.txt"),
    ("11-acr-private.png",
     "11 · ACR public network access disabled + private endpoint attached",
     "az acr show -g rg-opensandbox-demo -n acropensandboxdemo7075 --query \"{sku:sku.name,publicNetworkAccess:publicNetworkAccess,privateEndpoints:length(privateEndpointConnections)}\"",
     "11.txt"),
    ("12-sdk-e2e-terminal.png",
     "12 · RUN-4 SDK end-to-end — sandbox.create → exec → HELLO_FROM_REAL_OPENSANDBOX",
     "/tmp/osb-sdk/Scripts/python.exe evidence/runs/finish/sdk_e2e.py",
     "12.txt"),
    ("13-sandbox-pod.png",
     "13 · Live sandbox pod created by the SDK run",
     "kubectl get pods -n opensandbox -o wide",
     "13.txt"),
    ("14-sandbox-uname.png",
     "14 · uname -a inside the live sandbox",
     "kubectl exec -n opensandbox f14b86bd-924d-403e-b4ec-90954ae23fa4-0 -- uname -a",
     "14.txt"),
    ("16-kimi-via-osb.png",
     "16 · Kimi-K2.5 → OpenSandbox → fib(10) — verdict PASS",
     "AAD_TOKEN=$(az account get-access-token --resource https://cognitiveservices.azure.com -o tsv) /tmp/osb-sdk/Scripts/python.exe evidence/runs/finish/kimi_via_osb.py",
     "16.txt"),
    ("18-firewall-overview.png",
     "18 · Azure Firewall — Premium, private IP 10.10.10.4",
     "az network firewall show -g rg-opensandbox-dev -n afw-opensandbox-dev --query \"{state:provisioningState,sku:sku.tier,privateIp:ipConfigurations[0].privateIPAddress}\"",
     "18.txt"),
    ("19-firewall-rules.png",
     "19 · Firewall policy rule-collection groups",
     "az network firewall policy rule-collection-group list -g rg-opensandbox-dev --policy-name afwp-opensandbox-dev -o table",
     "19.txt"),
    ("20-udr-snet-kata.png",
     "20 · snet-kata subnet — UDR attached (forces egress through firewall)",
     "az network vnet subnet show -g rg-opensandbox-dev --vnet-name vnet-opensandbox-dev -n snet-kata --query \"{name:name,routeTable:routeTable.id,prefix:addressPrefix}\"",
     "20.txt"),
    ("21-acr-pe.png",
     "21 · ACR private endpoint in snet-pe (Approved/Succeeded)",
     "az network private-endpoint show -g rg-opensandbox-dev -n pe-acr-opensandbox-dev --query \"{name:name,state:provisioningState,subnet:subnet.id}\"",
     "21.txt"),
    ("22-acns-enabled.png",
     "22 · Advanced Container Networking Services — Observability + Security enabled",
     "az aks show -g rg-opensandbox-dev -n aks-opensandbox-dev --query \"networkProfile.advancedNetworking\"",
     "22.txt"),
    ("24-law-audit.png",
     "24 · audit-fast container — latest 3 stream-analytics audit blobs",
     "az storage blob list --account-name stasadevse3bwihj3in4s --container-name audit-fast --auth-mode login --query \"sort_by([],&properties.lastModified) | [-3:].{name:name,size:properties.contentLength}\" -o table",
     "24.txt"),
    ("25-aca-server.png",
     "25 · Container Apps in rg-opensandbox-dev (control plane on ACA)",
     "az containerapp list -g rg-opensandbox-dev -o table",
     "25.txt"),
]


def main():
    py = sys.executable
    script = str(ROOT / "_render.py")
    for filename, title, command, raw in SHOTS:
        raw_path = RAW / raw
        out_path = ROOT / filename
        if not raw_path.exists():
            print(f"!! missing raw: {raw_path}")
            continue
        spec = {
            "title": title,
            "command": command,
            "output": raw_path.read_text(encoding="utf-8", errors="replace"),
            "out": str(out_path),
        }
        proc = subprocess.run([py, script], input=json.dumps(spec),
                              text=True, capture_output=True)
        if proc.returncode != 0:
            print(f"!! render failed for {filename}: {proc.stderr}")
        else:
            print(proc.stdout.strip())


if __name__ == "__main__":
    main()
