"""Probe worker readiness for a tree rename without applying changes."""

from __future__ import annotations

import json
import urllib.request

worker_endpoint = "http://192.168.0.183:8765"
worker_token = "08heL611cn0k1GiiSktrAnx8ko5R18H9tOnHoTaM30U="

payload = {
    "action": "probe_tree_item_rename",
    "checkpoint": "probe_before_apply",
    "locator": [4, 0, 0, 1, 0],
    "expected_path": [
        "程序: V3-6-0-8new",
        "每次扫描执行型模块",
        "InitProgram:Fix Z axis drop",
        "书签",
        "/*报警*/",
    ],
    "expected_source": "/*报警*/",
    "target": "/*Alarm*/",
    "apply": False,
}

request = urllib.request.Request(
    f"{worker_endpoint}/v1/action",
    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {worker_token}",
        "Content-Type": "application/json",
    },
    method="POST",
)

try:
    with urllib.request.urlopen(request, timeout=30) as response:
        print(response.read().decode("utf-8"))
except urllib.error.HTTPError as exc:
    print(f"HTTP {exc.code}: {exc.read().decode('utf-8')}")
