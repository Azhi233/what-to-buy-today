"""停用持续 0 结果的无效词（GX10/GB10），结果导向：把请求留给有效词。"""
import json
import urllib.request

import app

tok = app.DASHBOARD_TOKEN
BASE = "http://127.0.0.1:5000/api"
DISABLE = {"GX10", "GB10"}


def call(method, path, body=None):
    req = urllib.request.Request(
        f"{BASE}{path}", method=method,
        headers={"Content-Type": "application/json", "X-Auth-Token": tok},
        data=json.dumps(body).encode() if body is not None else None,
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.status, r.read().decode()


def main():
    _, raw = call("GET", "/products")
    for p in json.loads(raw):
        if p["keyword"] in DISABLE and p["enabled"]:
            st, body = call("POST", f"/products/{p['id']}/toggle")
            print(f"停用 {p['keyword']} (id={p['id']}): HTTP {st} {body[:60]}")
        else:
            print(f"保留 {p['keyword']} enabled={p['enabled']}")


if __name__ == "__main__":
    main()