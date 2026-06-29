"""End-to-end HTTP + SSE smoke test against a running server (port 8077)."""

from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8077/api"


def call(path, method="GET", body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def stream(path, token, stop_on=("awaiting_decision", "resolved", "error"), max_events=200):
    headers = {"Authorization": f"Bearer {token}"}
    req = urllib.request.Request(BASE + path, headers=headers)
    seen = []
    tokens = 0
    max_seq = 0
    with urllib.request.urlopen(req, timeout=180) as r:
        for raw in r:
            line = raw.decode().strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "{}":
                continue
            ev = json.loads(payload)
            if isinstance(ev.get("seq"), int):
                max_seq = max(max_seq, ev["seq"])
            t = ev.get("type")
            if t == "token":
                tokens += 1
                continue
            seen.append(ev)
            print(f"   · [{t}] {ev.get('agent','')}: {ev.get('detail','')[:80]}")
            if t in stop_on or len(seen) >= max_events:
                break
    if tokens:
        print(f"   · (streamed {tokens} live tokens)")
    return seen, max_seq


def main():
    print("AI status:", call("/ai-status"))
    sess = call("/login", "POST", {"name": "Ananya Sharma", "aadhaar_last4": "1234"})
    token = sess["token"]
    print("Logged in as", sess["citizen_id"])

    sample = call("/sample-claim")
    claim = sample["claim"]
    case = call("/cases", "POST", claim, token)
    cid = case["case_id"]
    print("Created case", cid, "tier", case["tier"])

    call(f"/cases/{cid}/skip-response", "POST", None, token)
    print("Marked uncontested. Running pipeline…")
    print("Run:", call(f"/cases/{cid}/run", "POST", None, token))

    print("Pipeline events:")
    _, cursor = stream(f"/cases/{cid}/events?after=0", token)

    print("Accepting mediation…")
    print("Mediation:", call(f"/cases/{cid}/mediation", "POST", {"accept": True}, token))

    print("Resolution events (resuming from cursor):")
    stream(f"/cases/{cid}/events?after={cursor}", token, stop_on=("resolved", "error"))

    final = call(f"/cases/{cid}", token=token)
    print("\nFinal status:", final["status"])
    if final.get("resolution"):
        print("Relief:", final["resolution"]["relief_amount_display"],
              "| deadline:", final["resolution"]["compliance_deadline"],
              "| engine:", final["resolution"]["engine"])

    # IDOR check: a different citizen must NOT read this case.
    other = call("/login", "POST", {"name": "Mallory", "aadhaar_last4": "9999"})
    try:
        call(f"/cases/{cid}", token=other["token"])
        print("IDOR CHECK: FAIL — other user read the case!")
    except urllib.error.HTTPError as e:
        print(f"IDOR CHECK: PASS — other user blocked ({e.code})")


if __name__ == "__main__":
    import urllib.error
    main()
