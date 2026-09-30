"""Create the demo account and its two preloaded workspaces through the public API.

Idempotent: logs in if the account exists, reuses workspaces by name, and
re-uploading a document is a no-op (content-hash dedupe).

    cd backend && uv run python ../scripts/seed_demo.py --base-url https://<app>.onrender.com

The demo credentials are intentionally public (they're in the README): it's a
throwaway account that only contains the fictional sample documents.
"""

import argparse
import sys
from pathlib import Path

import httpx

DEMO_EMAIL = "demo@example.com"
DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo-password-2026"
SAMPLES = Path(__file__).resolve().parent.parent / "sample_docs"

WORKSPACES = {
    "Acme Corp HR": [
        SAMPLES / "workspace_a_acme_hr" / "acme_employee_handbook.pdf",
        SAMPLES / "workspace_a_acme_hr" / "acme_expense_policy.docx",
        # Contains a prompt-injection attempt; used to show it is ignored.
        SAMPLES / "prompt_injection" / "vendor_onboarding_notes.md",
    ],
    "Project Falcon": [
        SAMPLES / "workspace_b_project_falcon" / "falcon_product_spec.docx",
        SAMPLES / "workspace_b_project_falcon" / "falcon_weekly_sync_notes.md",
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()

    # Free-tier hosts can take ~60s to wake up.
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=120) as client:
        r = client.post(
            "/api/auth/login", json={"identifier": DEMO_USERNAME, "password": DEMO_PASSWORD}
        )
        if r.status_code == 401:
            r = client.post(
                "/api/auth/signup",
                json={
                    "email": DEMO_EMAIL,
                    "username": DEMO_USERNAME,
                    "password": DEMO_PASSWORD,
                    "confirm_password": DEMO_PASSWORD,
                },
            )
            print("created demo account")
        r.raise_for_status()

        existing = {w["name"]: w["id"] for w in client.get("/api/workspaces").json()}
        for name, files in WORKSPACES.items():
            ws_id = existing.get(name)
            if ws_id is None:
                ws_id = client.post("/api/workspaces", json={"name": name}).raise_for_status().json()["id"]
                print(f"created workspace {name!r}")
            for path in files:
                with path.open("rb") as fh:
                    r = client.post(
                        f"/api/workspaces/{ws_id}/documents", files={"file": (path.name, fh)}
                    )
                if r.status_code >= 400:
                    print(f"  ! {path.name}: {r.status_code} {r.text[:200]}", file=sys.stderr)
                    return 1
                body = r.json()
                state = "already present" if body["duplicate"] else f"{body['document']['chunk_count']} chunks"
                print(f"  {name} <- {path.name}: {state}")
    print(f"\nDemo login: {DEMO_USERNAME} (or {DEMO_EMAIL}) / {DEMO_PASSWORD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
