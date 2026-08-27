#!/usr/bin/env python3
"""
Codex Fast Service Tier Throughput Benchmark & Reproduction
Tests throughput differences on https://chatgpt.com/backend-api/codex/responses
based on client identification headers.
"""

import os
import sys
import json
import time
import base64
import subprocess
import tempfile

def load_env():
    """Load variables from .env file if present."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k not in os.environ:
                    os.environ[k] = v

def get_account_id_from_jwt(token):
    """Attempt to decode chatgpt_account_id from JWT payload."""
    try:
        parts = token.split(".")
        if len(parts) >= 2:
            payload = parts[1]
            rem = len(payload) % 4
            if rem > 0:
                payload += "=" * (4 - rem)
            data = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
            auth_info = data.get("https://api.openai.com/auth", {})
            return auth_info.get("chatgpt_account_id")
    except Exception:
        pass
    return None

def main():
    load_env()
    
    token = os.getenv("OPENAI_ACCESS_TOKEN") or os.getenv("CHATGPT_AUTH_TOKEN")
    if not token:
        print("ERROR: Missing OPENAI_ACCESS_TOKEN environment variable.")
        print("Please set it in your environment or in a .env file.")
        print("Example: OPENAI_ACCESS_TOKEN='eyJhbGci...'")
        sys.exit(1)

    account_id = os.getenv("CHATGPT_ACCOUNT_ID") or get_account_id_from_jwt(token)
    if not account_id:
        print("WARNING: CHATGPT_ACCOUNT_ID not found in env or JWT; proceeding without header.")

    model = os.getenv("MODEL", "gpt-5.6-sol")
    url = "https://chatgpt.com/backend-api/codex/responses"

    print("=" * 80)
    print("Codex Fast Service Tier Throughput Benchmark")
    print(f"Target URL : {url}")
    print(f"Model      : {model}")
    print(f"Account ID : {account_id or 'Not Set'}")
    print("=" * 80)

    test_scenarios = [
        {
            "name": "1. Standard Baseline (no service_tier)",
            "service_tier": None,
            "originator": "codex-tui",
            "user_agent": "codex-tui/0.149.1 (Arch Linux; x86_64)"
        },
        {
            "name": "2. OpenCode Fast (originator: opencode)",
            "service_tier": "priority",
            "originator": "opencode",
            "user_agent": "opencode/1.18.23 (linux-x64) runtime/bun/1.3.14"
        },
        {
            "name": "3. Codex Fast (originator & UA: codex-tui)",
            "service_tier": "priority",
            "originator": "codex-tui",
            "user_agent": "codex-tui/0.149.1 (Arch Linux; x86_64)"
        },
        {
            "name": "4. Fast (originator: codex-tui, OpenCode UA)",
            "service_tier": "priority",
            "originator": "codex-tui",
            "user_agent": "opencode/1.18.23 (linux-x64) runtime/bun/1.3.14"
        }
    ]

    prompt = "Output exactly the integers from 1 to 300 inclusive, one per line, with no commentary."
    results = []

    for scenario in test_scenarios:
        print(f"\nRunning: {scenario['name']} ...")

        # Build payload
        payload = {
            "model": model,
            "stream": True,
            "store": False,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}]
                }
            ]
        }
        if scenario["service_tier"]:
            payload["service_tier"] = scenario["service_tier"]

        # Build headers
        headers = {
            "Authorization": f"Bearer {token.replace('Bearer ', '')}",
            "Content-Type": "application/json",
            "Accept": "*/*",
            "session-id": "01a041d9-2039-7d00-80ec-5b8cc6f5a06e"
        }
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id
        if scenario["originator"]:
            headers["originator"] = scenario["originator"]
        if scenario["user_agent"]:
            headers["User-Agent"] = scenario["user_agent"]

        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as tf:
            json.dump(payload, tf)
            temp_payload_path = tf.name

        try:
            cmd = ["curl", "-sN", url, "-X", "POST", "-d", f"@{temp_payload_path}"]
            for k, v in headers.items():
                cmd.extend(["-H", f"{k}: {v}"])

            start_time = time.perf_counter()
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            
            first_delta_time = None
            last_delta_time = None
            full_text = ""
            lines_count = 0
            raw_lines = []

            for line in proc.stdout:
                raw_lines.append(line)
                if line.startswith("data: "):
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        if data.get("type") == "response.output_text.delta":
                            now = time.perf_counter()
                            if first_delta_time is None:
                                first_delta_time = now
                            last_delta_time = now
                            delta = data.get("delta", "")
                            full_text += delta
                            if "\n" in delta:
                                lines_count += delta.count("\n")
                    except Exception:
                        pass

            proc.wait()
            end_time = time.perf_counter()

            if not first_delta_time:
                err_msg = "".join(raw_lines)[:150].strip()
                print(f"  -> FAILED: {err_msg}")
                results.append({**scenario, "status": "FAIL", "error": err_msg})
                continue

            ttft = first_delta_time - start_time
            stream_duration = last_delta_time - first_delta_time
            total_time = end_time - start_time
            
            # 1..300 integer sequence = 603 tokens
            tokens = 603 if lines_count >= 290 else max(len(full_text.splitlines()), 1)
            stream_tps = tokens / stream_duration if stream_duration > 0 else 0
            overall_tps = tokens / total_time if total_time > 0 else 0

            print(f"  -> TTFT: {ttft:.2f}s | Stream Duration: {stream_duration:.2f}s | Speed: {stream_tps:.1f} tok/s")
            results.append({
                **scenario,
                "status": "OK",
                "ttft": ttft,
                "stream_duration": stream_duration,
                "total_time": total_time,
                "tokens": tokens,
                "stream_tps": stream_tps,
                "overall_tps": overall_tps
            })
        finally:
            if os.path.exists(temp_payload_path):
                os.remove(temp_payload_path)

        # Brief pause between test runs
        time.sleep(1)

    # Summary table
    baseline_tps = next((r["stream_tps"] for r in results if r.get("status") == "OK"), 1.0)
    print("\n" + "=" * 90)
    print(f"{'TEST SCENARIO':<45} | {'TTFT':<7} | {'STREAM TIME':<11} | {'STREAM TPS':<12} | {'BOOST'}")
    print("=" * 90)
    for r in results:
        if r.get("status") == "OK":
            boost = r["stream_tps"] / baseline_tps
            print(f"{r['name']:<45} | {r['ttft']:>5.2f}s | {r['stream_duration']:>9.2f}s | {r['stream_tps']:>6.1f} tok/s | {boost:>5.2f}x")
        else:
            print(f"{r['name']:<45} | {'FAIL':>7} | {'-':>11} | {'-':>12} | {r.get('error', '')[:15]}")
    print("=" * 90)

if __name__ == "__main__":
    main()
