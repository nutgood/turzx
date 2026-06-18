"""Grafana datasource-proxy client (queries Prometheus through Grafana).

Avoids exposing Prometheus directly: every PromQL query goes through
``{GRAFANA_URL}/api/datasources/proxy/uid/prometheus/...`` with a service-account token.

Env: GRAFANA_URL (default the homelab tailnet host), GRAFANA_TOKEN (or ./.grafana_token).
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import ROOT

GURL = os.environ.get("GRAFANA_URL", "https://htalos-grafana.feist-boa.ts.net").rstrip("/")
_TOKFILE = os.path.join(ROOT, ".grafana_token")
TOKEN = os.environ.get("GRAFANA_TOKEN", "") or (
    open(_TOKFILE).read().strip() if os.path.exists(_TOKFILE) else "")
PROXY = f"{GURL}/api/datasources/proxy/uid/prometheus/api/v1/query"


def query(expr):
    """Run one PromQL query. Returns [(labels_dict, float_value), ...]."""
    data = urllib.parse.urlencode({"query": expr}).encode()
    req = urllib.request.Request(PROXY, data=data, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=12) as r:
        res = json.load(r)
    d = res.get("data", {})
    if d.get("resultType") == "scalar":               # scalar()/scalar() expressions
        try:
            return [({}, float(d["result"][1]))]
        except (KeyError, ValueError, IndexError):
            return []
    out = []
    for s in d.get("result", []):
        try:
            out.append((s.get("metric", {}), float(s["value"][1])))
        except (KeyError, ValueError):
            pass
    return out


def query_many(exprs, max_workers=12):
    """Run many queries concurrently. Returns {expr: result}; failed queries → []."""
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(query, e): e for e in exprs}
        for fu, e in futs.items():
            try:
                results[e] = fu.result()
            except Exception as err:
                results[e] = []
                print(f"query failed: {e[:40]}... {err}", file=sys.stderr)
    return results
