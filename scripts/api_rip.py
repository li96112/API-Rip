#!/usr/bin/env python3
"""API-Rip: Parse captured API traffic, deduplicate, infer patterns,
generate OpenAPI 3.0 spec and Postman Collection v2.1.
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from urllib.parse import urlparse, parse_qs, urlencode


# ---------------------------------------------------------------------------
# URL pattern detection — parameterize IDs
# ---------------------------------------------------------------------------

# Patterns that look like dynamic path segments
ID_PATTERNS = [
    (r'/\d+', '/{id}'),                          # /123
    (r'/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '/{uuid}'),  # UUID
    (r'/[0-9a-f]{24}', '/{objectId}'),            # MongoDB ObjectId
    (r'/[0-9a-f]{32,64}', '/{hash}'),             # Hash/token
    (r'/[A-Za-z0-9_-]{20,}', '/{token}'),         # Long tokens (careful)
]


def parameterize_path(path):
    """Replace dynamic path segments with parameter placeholders."""
    parts = path.strip('/').split('/')
    result = []
    params = []

    for part in parts:
        matched = False
        # Pure numeric
        if re.match(r'^\d+$', part):
            result.append('{id}')
            params.append(('id', 'integer', part))
            matched = True
        # UUID
        elif re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', part, re.I):
            result.append('{uuid}')
            params.append(('uuid', 'string', part))
            matched = True
        # MongoDB ObjectId
        elif re.match(r'^[0-9a-f]{24}$', part, re.I):
            result.append('{objectId}')
            params.append(('objectId', 'string', part))
            matched = True
        # Slug with mixed content that looks like an ID (has digits + letters, > 15 chars)
        elif len(part) > 15 and re.match(r'^[A-Za-z0-9_-]+$', part) and re.search(r'\d', part):
            result.append('{token}')
            params.append(('token', 'string', part))
            matched = True

        if not matched:
            result.append(part)

    return '/' + '/'.join(result), params


def infer_json_schema(obj, max_depth=5, depth=0):
    """Infer JSON Schema from a Python object."""
    if depth >= max_depth:
        return {}

    if obj is None:
        return {"type": "null"}
    elif isinstance(obj, bool):
        return {"type": "boolean"}
    elif isinstance(obj, int):
        return {"type": "integer"}
    elif isinstance(obj, float):
        return {"type": "number"}
    elif isinstance(obj, str):
        # Detect formats
        if re.match(r'^\d{4}-\d{2}-\d{2}', obj):
            return {"type": "string", "format": "date-time"}
        elif re.match(r'^https?://', obj):
            return {"type": "string", "format": "uri"}
        elif re.match(r'^[^@]+@[^@]+\.[^@]+$', obj):
            return {"type": "string", "format": "email"}
        return {"type": "string"}
    elif isinstance(obj, list):
        if not obj:
            return {"type": "array", "items": {}}
        # Use first non-null item as schema
        for item in obj:
            if item is not None:
                return {"type": "array", "items": infer_json_schema(item, max_depth, depth + 1)}
        return {"type": "array", "items": {}}
    elif isinstance(obj, dict):
        properties = {}
        required = []
        for key, val in obj.items():
            properties[key] = infer_json_schema(val, max_depth, depth + 1)
            if val is not None:
                required.append(key)
        schema = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required[:20]  # Limit
        return schema

    return {}


# ---------------------------------------------------------------------------
# Traffic analyzer
# ---------------------------------------------------------------------------

class APIAnalyzer:
    def __init__(self, captures, base_url=None, no_filter=False):
        self.captures = captures
        self.base_url = base_url
        self.no_filter = no_filter
        self.endpoints = defaultdict(list)  # (method, pattern) -> [captures]

    def analyze(self):
        """Group captures into deduplicated endpoints."""
        for cap in self.captures:
            # Skip non-API requests
            url = cap.get("url", "")
            if not self._is_api_request(cap):
                continue

            parsed = urlparse(url)
            method = cap.get("method", "GET").upper()
            path = parsed.path

            # Parameterize path
            pattern, path_params = parameterize_path(path)

            key = (method, pattern)
            self.endpoints[key].append({
                **cap,
                "pattern": pattern,
                "path_params": path_params,
                "parsed_query": parse_qs(parsed.query),
                "host": parsed.netloc,
                "scheme": parsed.scheme,
            })

        return self._build_report()

    def _is_api_request(self, cap):
        """Filter out non-API requests (static assets, tracking, etc.)."""
        url = cap.get("url", "")
        parsed = urlparse(url)

        # Always skip static assets (even with --no-filter)
        skip_ext = ('.js', '.css', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico',
                     '.woff', '.woff2', '.ttf', '.eot', '.map', '.webp')
        if any(parsed.path.lower().endswith(ext) for ext in skip_ext):
            return False

        # Always skip common tracking/analytics
        skip_domains = ('google-analytics.com', 'googletagmanager.com', 'facebook.net',
                        'doubleclick.net', 'analytics.', 'fonts.googleapis.com',
                        'fonts.gstatic.com')
        if any(d in parsed.netloc.lower() for d in skip_domains):
            return False

        # --no-filter: keep everything that isn't static/tracking
        if self.no_filter:
            return True

        content_type = cap.get("contentType", "")

        # Prefer JSON APIs
        if cap.get("isJson"):
            return True

        # Accept if it looks like an API path
        api_indicators = ['/api/', '/v1/', '/v2/', '/v3/', '/graphql', '/rest/',
                          '/rpc/', '/json', '/_api/', '/data/']
        if any(ind in parsed.path.lower() for ind in api_indicators):
            return True

        # Accept XHR/fetch with JSON content type
        if 'json' in content_type.lower():
            return True

        # Accept if method is not GET (likely API)
        if cap.get("method", "GET").upper() != "GET":
            return True

        return False

    def _build_report(self):
        """Build a structured report of all discovered endpoints."""
        if not self.base_url:
            # Infer base URL from first capture
            if self.captures:
                parsed = urlparse(self.captures[0]["url"])
                self.base_url = f"{parsed.scheme}://{parsed.netloc}"
            else:
                self.base_url = "https://unknown"

        endpoints = []
        for (method, pattern), caps in sorted(self.endpoints.items()):
            # Use the first successful response as the example
            success_caps = [c for c in caps if 200 <= c.get("status", 0) < 300]
            example = success_caps[0] if success_caps else caps[0]

            # Collect all unique query parameters across captures
            all_query_params = set()
            for c in caps:
                for k in c.get("parsed_query", {}).keys():
                    all_query_params.add(k)

            # Infer response schema
            response_schema = {}
            if example.get("isJson") and example.get("responseBody"):
                response_schema = infer_json_schema(example["responseBody"])

            # Infer request body schema
            request_schema = {}
            if example.get("requestBody"):
                try:
                    body = json.loads(example["requestBody"])
                    request_schema = infer_json_schema(body)
                except (json.JSONDecodeError, TypeError):
                    pass

            endpoints.append({
                "method": method,
                "pattern": pattern,
                "hits": len(caps),
                "example_url": example.get("url", ""),
                "status_codes": list(set(c.get("status", 0) for c in caps)),
                "content_type": example.get("contentType", ""),
                "path_params": example.get("path_params", []),
                "query_params": sorted(all_query_params),
                "request_headers": example.get("requestHeaders", {}),
                "request_body": example.get("requestBody"),
                "request_schema": request_schema,
                "response_body_sample": example.get("responseBody"),
                "response_schema": response_schema,
                "response_size": example.get("responseSize", 0),
                "avg_latency": round(sum(c.get("latency", 0) for c in caps) / len(caps)),
                "host": example.get("host", ""),
            })

        # Group by host
        hosts = defaultdict(list)
        for ep in endpoints:
            hosts[ep["host"]].append(ep)

        return {
            "base_url": self.base_url,
            "total_captures": len(self.captures),
            "api_endpoints": len(endpoints),
            "endpoints": endpoints,
            "hosts": {h: len(eps) for h, eps in hosts.items()},
        }


# ---------------------------------------------------------------------------
# OpenAPI 3.0 generator
# ---------------------------------------------------------------------------

def generate_openapi(report, title=None):
    """Generate OpenAPI 3.0 spec from analysis report."""
    base = report["base_url"]
    parsed = urlparse(base)

    spec = {
        "openapi": "3.0.3",
        "info": {
            "title": title or f"API discovered from {parsed.netloc}",
            "description": f"Auto-generated by API-Rip from traffic capture on {parsed.netloc}",
            "version": "1.0.0",
        },
        "servers": [{"url": base}],
        "paths": {},
    }

    for ep in report["endpoints"]:
        path = ep["pattern"]
        method = ep["method"].lower()

        operation = {
            "summary": f"{ep['method']} {ep['pattern']}",
            "operationId": _make_operation_id(ep["method"], ep["pattern"]),
            "responses": {},
        }

        # Path parameters
        if ep.get("path_params"):
            operation["parameters"] = []
            for name, ptype, example in ep["path_params"]:
                operation["parameters"].append({
                    "name": name,
                    "in": "path",
                    "required": True,
                    "schema": {"type": ptype},
                    "example": example,
                })

        # Query parameters
        if ep.get("query_params"):
            if "parameters" not in operation:
                operation["parameters"] = []
            for qp in ep["query_params"]:
                operation["parameters"].append({
                    "name": qp,
                    "in": "query",
                    "schema": {"type": "string"},
                })

        # Request body
        if ep.get("request_schema") and ep["method"] in ("POST", "PUT", "PATCH"):
            operation["requestBody"] = {
                "content": {
                    "application/json": {
                        "schema": ep["request_schema"],
                    }
                }
            }

        # Responses
        for status in sorted(set(ep.get("status_codes", [200]))):
            status_str = str(status)
            resp = {"description": _status_description(status)}
            if ep.get("response_schema") and 200 <= status < 300:
                resp["content"] = {
                    "application/json": {
                        "schema": ep["response_schema"],
                    }
                }
            operation["responses"][status_str] = resp

        if path not in spec["paths"]:
            spec["paths"][path] = {}
        spec["paths"][path][method] = operation

    return spec


def _make_operation_id(method, pattern):
    """Generate a readable operationId from method + path."""
    parts = pattern.strip('/').replace('{', '').replace('}', '').split('/')
    words = [method.lower()] + [p for p in parts if p]
    return '_'.join(words)[:60]


def _status_description(status):
    """Return a short description for HTTP status codes."""
    descriptions = {
        200: "OK", 201: "Created", 204: "No Content",
        301: "Moved Permanently", 302: "Found", 304: "Not Modified",
        400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
        404: "Not Found", 405: "Method Not Allowed", 409: "Conflict",
        422: "Unprocessable Entity", 429: "Too Many Requests",
        500: "Internal Server Error", 502: "Bad Gateway", 503: "Service Unavailable",
    }
    return descriptions.get(status, f"HTTP {status}")


# ---------------------------------------------------------------------------
# Postman Collection v2.1 generator
# ---------------------------------------------------------------------------

def generate_postman(report, name=None):
    """Generate Postman Collection v2.1 from analysis report."""
    base = report["base_url"]
    parsed = urlparse(base)

    collection = {
        "info": {
            "name": name or f"API-Rip: {parsed.netloc}",
            "description": f"Auto-captured API endpoints from {parsed.netloc}",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "variable": [
            {"key": "baseUrl", "value": base, "type": "string"},
        ],
        "item": [],
    }

    # Group by first path segment as folders
    folders = defaultdict(list)
    for ep in report["endpoints"]:
        parts = ep["pattern"].strip('/').split('/')
        folder_name = parts[0] if parts else "root"
        folders[folder_name].append(ep)

    for folder_name, endpoints in sorted(folders.items()):
        folder = {
            "name": folder_name,
            "item": [],
        }

        for ep in endpoints:
            parsed_url = urlparse(ep["example_url"])

            # Build URL
            url_parts = ep["pattern"].strip('/').split('/')
            path = [p.replace('{', ':').replace('}', '') for p in url_parts]

            item = {
                "name": f"{ep['method']} {ep['pattern']}",
                "request": {
                    "method": ep["method"],
                    "header": [
                        {"key": k, "value": v}
                        for k, v in ep.get("requestHeaders", {}).items()
                        if k.lower() not in ("host", "user-agent", "accept-encoding",
                                              "connection", "content-length")
                    ],
                    "url": {
                        "raw": "{{baseUrl}}" + ep["pattern"],
                        "host": ["{{baseUrl}}"],
                        "path": path,
                    },
                },
            }

            # Query params
            if ep.get("query_params"):
                item["request"]["url"]["query"] = [
                    {"key": qp, "value": "", "description": ""}
                    for qp in ep["query_params"]
                ]

            # Request body
            if ep.get("requestBody") and ep["method"] in ("POST", "PUT", "PATCH"):
                item["request"]["body"] = {
                    "mode": "raw",
                    "raw": ep["requestBody"] if isinstance(ep["requestBody"], str)
                           else json.dumps(ep["requestBody"], indent=2),
                    "options": {"raw": {"language": "json"}},
                }

            # Example response
            if ep.get("response_body_sample"):
                item["response"] = [{
                    "name": f"Example {ep['status_codes'][0] if ep['status_codes'] else 200}",
                    "status": _status_description(ep["status_codes"][0] if ep["status_codes"] else 200),
                    "code": ep["status_codes"][0] if ep["status_codes"] else 200,
                    "body": json.dumps(ep["response_body_sample"], indent=2, ensure_ascii=False)[:5000],
                }]

            folder["item"].append(item)

        collection["item"].append(folder)

    return collection


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------

def generate_report_md(report, openapi_path=None, postman_path=None):
    """Generate a human-readable Markdown summary."""
    lines = []
    base = report["base_url"]
    parsed = urlparse(base)

    lines.append(f"# API-Rip Report: {parsed.netloc}\n")
    lines.append(f"> Base URL: `{base}`")
    lines.append(f"> Total requests captured: {report['total_captures']}")
    lines.append(f"> API endpoints found: {report['api_endpoints']}")

    if report.get("hosts"):
        hosts = report["hosts"]
        if len(hosts) > 1:
            lines.append(f"> Hosts: {', '.join(f'`{h}` ({c})' for h, c in hosts.items())}")

    if openapi_path:
        lines.append(f"> OpenAPI spec: `{openapi_path}`")
    if postman_path:
        lines.append(f"> Postman collection: `{postman_path}`")

    lines.append("\n---\n")

    # Endpoint table
    lines.append("## Discovered Endpoints\n")
    lines.append("| Method | Path | Hits | Status | Latency | Size |")
    lines.append("|--------|------|------|--------|---------|------|")

    for ep in report["endpoints"]:
        statuses = ", ".join(str(s) for s in ep["status_codes"])
        size_kb = ep.get("response_size", 0) / 1024
        lines.append(
            f"| `{ep['method']}` | `{ep['pattern']}` | {ep['hits']} | "
            f"{statuses} | {ep['avg_latency']}ms | {size_kb:.1f}KB |"
        )

    lines.append("")

    # Detailed endpoint info
    lines.append("## Endpoint Details\n")
    for i, ep in enumerate(report["endpoints"], 1):
        lines.append(f"### {i}. `{ep['method']} {ep['pattern']}`\n")
        lines.append(f"- Example: `{ep['example_url'][:100]}`")
        lines.append(f"- Status: {', '.join(str(s) for s in ep['status_codes'])}")
        lines.append(f"- Avg latency: {ep['avg_latency']}ms")
        lines.append(f"- Content-Type: `{ep['content_type']}`")

        if ep.get("path_params"):
            lines.append(f"- Path params: {', '.join(f'`{p[0]}` ({p[1]})' for p in ep['path_params'])}")

        if ep.get("query_params"):
            lines.append(f"- Query params: {', '.join(f'`{q}`' for q in ep['query_params'])}")

        if ep.get("request_body"):
            lines.append(f"\n**Request Body:**\n```json\n{_truncate_json(ep['request_body'])}\n```")

        if ep.get("response_body_sample"):
            lines.append(f"\n**Response Sample:**\n```json\n{_truncate_json(ep['response_body_sample'])}\n```")

        lines.append("---\n")

    return "\n".join(lines)


def _truncate_json(obj, max_len=500):
    """Pretty-print JSON, truncated."""
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except:
            return obj[:max_len]
    s = json.dumps(obj, indent=2, ensure_ascii=False)
    if len(s) > max_len:
        return s[:max_len] + "\n... [truncated]"
    return s


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="API-Rip: Captured traffic → OpenAPI + Postman")
    parser.add_argument("--input", "-i", required=True, help="Captured traffic JSON file")
    parser.add_argument("--base-url", help="Override base URL")
    parser.add_argument("--title", help="API title for specs")
    parser.add_argument("--openapi", help="Output OpenAPI 3.0 JSON path")
    parser.add_argument("--postman", help="Output Postman Collection JSON path")
    parser.add_argument("--report", "-r", help="Output Markdown report path")
    parser.add_argument("--all", "-a", help="Output prefix (generates all formats: prefix_openapi.json, prefix_postman.json, prefix_report.md)")
    parser.add_argument("--no-filter", action="store_true", help="Skip API detection filter — keep all non-static requests")
    args = parser.parse_args()

    # Load captures
    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Handle double-encoded JSON (browse `js` command wraps result in quotes)
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            print(f"[!] Error: input file contains a string that is not valid JSON", file=sys.stderr)
            sys.exit(1)

    captures = data.get("captures", data) if isinstance(data, dict) else data

    if not isinstance(captures, list):
        print(f"[!] Error: expected a list of captures, got {type(captures).__name__}", file=sys.stderr)
        sys.exit(1)

    print(f"[*] Loaded {len(captures)} captured requests")

    # Analyze
    analyzer = APIAnalyzer(captures, args.base_url, no_filter=args.no_filter)
    report = analyzer.analyze()

    print(f"[+] Found {report['api_endpoints']} API endpoints")

    # Output paths
    openapi_path = args.openapi
    postman_path = args.postman
    report_path = args.report

    if args.all:
        prefix = args.all
        openapi_path = openapi_path or f"{prefix}_openapi.json"
        postman_path = postman_path or f"{prefix}_postman.json"
        report_path = report_path or f"{prefix}_report.md"

    # Generate OpenAPI
    if openapi_path:
        spec = generate_openapi(report, args.title)
        with open(openapi_path, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=2, ensure_ascii=False)
        print(f"[+] OpenAPI 3.0 spec: {openapi_path}")

    # Generate Postman
    if postman_path:
        collection = generate_postman(report, args.title)
        with open(postman_path, "w", encoding="utf-8") as f:
            json.dump(collection, f, indent=2, ensure_ascii=False)
        print(f"[+] Postman Collection: {postman_path}")

    # Generate report
    if report_path:
        md = generate_report_md(report, openapi_path, postman_path)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"[+] Report: {report_path}")

    # Summary
    if not any([openapi_path, postman_path, report_path]):
        # Just print summary
        md = generate_report_md(report)
        print(md)


if __name__ == "__main__":
    main()
