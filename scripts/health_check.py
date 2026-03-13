#!/usr/bin/env python3
"""
System health check script.
Verifies all Railway services are reachable and operational.

Usage:
  API_URL=https://your-api.railway.app python scripts/health_check.py
  python scripts/health_check.py --api https://your-api.railway.app
"""
import argparse
import asyncio
import sys
import os

try:
    import httpx
except ImportError:
    print("Install httpx: pip install httpx")
    sys.exit(1)

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


async def check_api(base_url: str, api_key: str = "") -> dict:
    headers = {"X-API-Key": api_key} if api_key else {}
    results = {}

    async with httpx.AsyncClient(timeout=15, headers=headers) as client:
        # /health
        try:
            r = await client.get(f"{base_url}/health")
            data = r.json()
            results["api_health"] = {
                "ok":     r.status_code == 200,
                "status": data.get("status"),
                "db":     data.get("database"),
                "latency_ms": round(r.elapsed.total_seconds() * 1000),
            }
        except Exception as exc:
            results["api_health"] = {"ok": False, "error": str(exc)}

        # Key API endpoints
        for name, path in [
            ("leads_stats",    "/api/v1/leads/stats/summary"),
            ("campaign_summary","/api/v1/campaigns/summary"),
            ("inbox_status",   "/api/v1/inbox/status"),
            ("activity_feed",  "/api/v1/activity/feed?limit=1"),
        ]:
            try:
                r = await client.get(f"{base_url}{path}")
                results[name] = {
                    "ok":         r.status_code == 200,
                    "status_code": r.status_code,
                    "latency_ms": round(r.elapsed.total_seconds() * 1000),
                }
            except Exception as exc:
                results[name] = {"ok": False, "error": str(exc)}

    return results


def print_result(label: str, result: dict) -> bool:
    ok      = result.get("ok", False)
    icon    = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
    latency = f"  {CYAN}{result['latency_ms']}ms{RESET}" if "latency_ms" in result else ""
    detail  = ""
    if not ok and "error" in result:
        detail = f"  {RED}{result['error'][:60]}{RESET}"
    elif "status" in result:
        detail = f"  db={result.get('db', '?')}"
    elif "status_code" in result and result["status_code"] != 200:
        detail = f"  {YELLOW}HTTP {result['status_code']}{RESET}"
    print(f"  {icon} {label:<30}{latency}{detail}")
    return ok


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=os.environ.get("API_URL", "http://localhost:8000"))
    parser.add_argument("--key", default=os.environ.get("API_SECRET_KEY", ""))
    args = parser.parse_args()

    print(f"\n{BOLD}Lead Engine — System Health Check{RESET}")
    print(f"API: {CYAN}{args.api}{RESET}\n")

    results = await check_api(args.api, args.key)
    passed  = 0
    total   = len(results)

    print(f"{BOLD}API Endpoints{RESET}")
    for name, result in results.items():
        if print_result(name.replace("_", " ").title(), result):
            passed += 1

    print()
    all_ok = passed == total
    if all_ok:
        print(f"{GREEN}{BOLD}All {total} checks passed ✓{RESET}\n")
        return 0
    else:
        print(f"{RED}{BOLD}{total - passed}/{total} checks failed{RESET}\n")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
