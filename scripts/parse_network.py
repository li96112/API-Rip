#!/usr/bin/env python3
"""API-Rip: Parse $B network output into capture JSON format.

This is an alternative input method when JS interception doesn't work
(e.g., SPA with full page navigation, interceptor gets wiped on reload).

Usage:
  $B network > /tmp/network_log.txt
  python3 parse_network.py --input /tmp/network_log.txt --output /tmp/api_captures.json
"""

import argparse
import json
import re
import sys


def parse_network_log(text):
    """Parse gstack browse network log lines into capture objects."""
    captures = []
    cap_id = 0

    for line in text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue

        # Format: METHOD URL → STATUS (LATENCYms, SIZEB)
        m = re.match(
            r'^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+'
            r'(https?://\S+)\s+'
            r'→\s+'
            r'(\d+|pending)\s*'
            r'\((\d+|[\?])ms,\s*([\d?]+)B?\)',
            line
        )

        # Fallback: just METHOD URL (with optional status)
        if not m:
            m2 = re.match(
                r'^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+'
                r'(https?://\S+)'
                r'(?:\s+(\d+))?',
                line
            )
            if m2:
                method = m2.group(1)
                url = m2.group(2)
                status = int(m2.group(3)) if m2.group(3) else 0
                latency = 0
                size = 0
                cap_id += 1
                captures.append({
                    "id": cap_id,
                    "type": "network",
                    "url": url,
                    "method": method,
                    "requestHeaders": {},
                    "requestBody": None,
                    "status": status,
                    "statusText": "",
                    "responseHeaders": {},
                    "responseBody": None,
                    "responseSize": size,
                    "contentType": _guess_content_type(url),
                    "isJson": _looks_like_api(url, method),
                    "latency": latency,
                    "timestamp": "",
                })
            continue

        method = m.group(1)
        url = m.group(2)
        status = int(m.group(3)) if m.group(3) != 'pending' else 0
        latency = int(m.group(4)) if m.group(4) != '?' else 0
        size = int(m.group(5)) if m.group(5) != '?' else 0

        cap_id += 1
        captures.append({
            "id": cap_id,
            "type": "network",
            "url": url,
            "method": method,
            "requestHeaders": {},
            "requestBody": None,
            "status": status,
            "statusText": "",
            "responseHeaders": {},
            "responseBody": None,
            "responseSize": size,
            "contentType": _guess_content_type(url),
            "isJson": _looks_like_api(url, method),
            "latency": latency,
            "timestamp": "",
        })

    return captures


def _guess_content_type(url):
    """Guess content type from URL."""
    lower = url.lower()
    if any(ext in lower for ext in ['.json', '/api/', '/v1/', '/v2/', '/graphql']):
        return 'application/json'
    if any(ext in lower for ext in ['.js']):
        return 'application/javascript'
    if any(ext in lower for ext in ['.css']):
        return 'text/css'
    if any(ext in lower for ext in ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico']):
        return 'image/*'
    if any(ext in lower for ext in ['.woff', '.woff2', '.ttf', '.eot']):
        return 'font/*'
    return 'text/html'


def _looks_like_api(url, method):
    """Heuristic: does this URL look like an API call?"""
    lower = url.lower()
    if method != 'GET':
        return True
    api_indicators = ['/api/', '/v1/', '/v2/', '/v3/', '/graphql', '/rest/',
                      '/rpc/', '.json', '/_api/', '/data/', '/query']
    return any(ind in lower for ind in api_indicators)


def main():
    parser = argparse.ArgumentParser(description="Parse $B network output")
    parser.add_argument("--input", "-i", required=True, help="Network log file")
    parser.add_argument("--output", "-o", required=True, help="Output JSON file")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        text = f.read()

    captures = parse_network_log(text)
    api_count = sum(1 for c in captures if c["isJson"])

    output = {
        "captures": captures,
        "count": len(captures),
        "api_count": api_count,
        "source": "network_log",
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"[+] Parsed {len(captures)} requests ({api_count} likely API calls)")
    print(f"[+] Saved to {args.output}")


if __name__ == "__main__":
    main()
