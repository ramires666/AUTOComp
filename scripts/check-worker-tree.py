"""Check the current KV STUDIO project-tree state via the remote worker."""

from __future__ import annotations

import json
import urllib.request

worker_endpoint = "http://192.168.0.183:8765"
worker_token = "08heL611cn0k1GiiSktrAnx8ko5R18H9tOnHoTaM30U="

payload = {
    "action": "inventory_project_tree",
    "apply": False,
    "checkpoint": "",
    "expand_all": False,
    "restore_state": True,
}

request = urllib.request.Request(
    f"{worker_endpoint}/v1/action",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {worker_token}",
        "Content-Type": "application/json",
    },
    method="POST",
)

with urllib.request.urlopen(request, timeout=30) as response:
    print(response.read().decode("utf-8"))
