---
name: api-rip
description: Browse any website, intercept all API requests (fetch/XHR), deduplicate endpoints, infer RESTful patterns, and generate OpenAPI 3.0 spec + Postman Collection v2.1. Auto-parameterizes IDs/UUIDs, infers JSON schemas from responses.
metadata: {"openclaw":{"emoji":"🔌","requires":{"bins":["python3"]},"homepage":"https://github.com/li96112/API-Rip"}}
---

# API-Rip — API 逆向抓取器

> 打开网站，偷走完整 API 文档

浏览任意网站，自动拦截所有 API 请求，去重归类后生成 OpenAPI 3.0 规范 + Postman Collection，支持自动参数化 ID/UUID、推断 JSON Schema。

## Agent 调用方式

### 标准流程（浏览器模式）

注入拦截器后会自动通过 Performance API 发现页面加载时已完成的 API 请求，并重新 fetch 获取完整响应（含 body/headers），解决"注入太晚"的时序问题。

```bash
# ---- Step 1: 设置浏览器 ----
B=~/.claude/skills/gstack/browse/dist/browse

# ---- Step 2: 访问目标页面 ----
$B goto <URL>

# ---- Step 3: 注入拦截器（自动回补已发出的请求） ----
$B js "$(cat {baseDir}/scripts/api_intercept.js)"

# ---- Step 4: 等待 replay 请求完成 ----
sleep 2

# ---- Step 5: 浏览更多页面以捕获更多 API（可选） ----
# 每次 goto 新页面后必须重新注入拦截器（JS 上下文不跨页面）
$B goto <OTHER_URL>
$B js "$(cat {baseDir}/scripts/api_intercept.js)"
$B wait --networkidle
# 页面内的点击/交互不需要重新注入，会被当前页的拦截器捕获
$B click @e5
$B wait --networkidle

# ---- Step 6: 收集捕获的流量 ----
# collect 会自动等待 replay 请求完成后再导出
$B js "$(cat {baseDir}/scripts/api_collect.js)" > /tmp/api_captures.json

# ---- Step 7: 生成 API 文档 ----
python3 {baseDir}/scripts/api_rip.py \
  --input /tmp/api_captures.json \
  --all /tmp/api_rip_<DOMAIN>

# 输出三个文件：
#   /tmp/api_rip_<DOMAIN>_openapi.json   — OpenAPI 3.0 规范
#   /tmp/api_rip_<DOMAIN>_postman.json   — Postman Collection v2.1
#   /tmp/api_rip_<DOMAIN>_report.md      — 可读的 API 文档

# ---- Step 8: 展示报告 ----
# 读取 /tmp/api_rip_<DOMAIN>_report.md 展示给用户
```

> **重要**：每次 `$B goto` 导航到新页面后，必须重新 `$B js` 注入拦截器（gstack browse 的 JS 上下文不跨页面）。拦截器每次注入都会自动回补该页面已完成的请求。

### 深度捕获（登录后的 API）

```bash
# 先登录
$B goto <LOGIN_URL>
$B fill @e3 "username"
$B fill @e4 "password"
$B click @e5
$B wait --networkidle

# 然后注入拦截器
$B js "$(cat {baseDir}/scripts/api_intercept.js)"

# 浏览需要认证的页面
$B goto <PROTECTED_URL>
$B wait --networkidle

# 收集并生成
$B js "$(cat {baseDir}/scripts/api_collect.js)" > /tmp/api_captures.json
python3 {baseDir}/scripts/api_rip.py -i /tmp/api_captures.json --all /tmp/api_rip
```

### 网络日志模式（备选，更可靠）

当 JS 拦截器因为页面跳转被重置时，或首屏请求在注入前已发出时，用浏览器内置的网络日志（推荐作为首选方式）：

```bash
B=~/.claude/skills/gstack/browse/dist/browse

# 清空之前的日志
$B network --clear

# 浏览目标网站
$B goto <URL>
$B wait --networkidle

# 多浏览几个页面
$B click @e5
$B wait --networkidle

# 导出网络日志
$B network > /tmp/network_log.txt

# 解析网络日志为标准格式
python3 {baseDir}/scripts/parse_network.py \
  -i /tmp/network_log.txt \
  -o /tmp/api_captures.json

# 生成 API 文档
python3 {baseDir}/scripts/api_rip.py \
  -i /tmp/api_captures.json \
  --all /tmp/api_rip_<DOMAIN>

# 如果 API 路径不含 /api/ /v1/ 等标准前缀，加 --no-filter 保留所有非静态请求
python3 {baseDir}/scripts/api_rip.py \
  -i /tmp/api_captures.json \
  --no-filter \
  --all /tmp/api_rip_<DOMAIN>
```

> 网络日志模式不需要注入 JS，但无法获取请求/响应 body。适合先快速发现端点，再用 JS 拦截器模式获取详细数据。
> 对于非标准 API 路径的站点（如 components.rcyq.net），建议加 `--no-filter`。

### 只生成特定格式

```bash
# 只要 OpenAPI
python3 {baseDir}/scripts/api_rip.py -i captures.json --openapi api_spec.json

# 只要 Postman
python3 {baseDir}/scripts/api_rip.py -i captures.json --postman collection.json

# 只要报告
python3 {baseDir}/scripts/api_rip.py -i captures.json --report api_doc.md
```

### 触发关键词
- "抓取 API" / "API 逆向" / "偷 API"
- "API-Rip" / "api rip"
- "生成 OpenAPI" / "生成 Swagger" / "生成 Postman"
- "这个网站有哪些 API"
- "抓取这个网站的接口"

## 核心能力

| 能力 | 说明 |
|------|------|
| **请求拦截** | 拦截所有 fetch + XHR，记录 URL/method/headers/body/status/response |
| **智能过滤** | 自动排除静态资源（.js/.css/.png）、分析脚本（GA/GTM）、CDN 资源 |
| **路径参数化** | `/users/123` → `/users/{id}`，自动识别数字 ID / UUID / ObjectId / Token |
| **去重归类** | 相同 pattern + method 的请求合并为一个 endpoint |
| **Schema 推断** | 从响应 JSON 自动推断完整的 JSON Schema（类型、格式、必填字段） |
| **请求体推断** | POST/PUT/PATCH 的请求体也会推断 Schema |
| **多主机** | 自动检测并分组不同域名的 API 请求 |
| **OpenAPI 3.0** | 生成标准 OpenAPI 3.0.3 规范（含 paths/parameters/schemas/responses） |
| **Postman v2.1** | 生成可直接导入 Postman 的 Collection（含文件夹分组、变量、示例） |
| **Markdown 报告** | 人可读的 API 文档（端点表格 + 详细参数 + 请求/响应示例） |

## 输出示例

```
# API-Rip Report: api.example.com

> Total requests captured: 47
> API endpoints found: 12

| Method | Path              | Hits | Status | Latency |
|--------|-------------------|------|--------|---------|
| GET    | /api/users/{id}   | 5    | 200    | 120ms   |
| POST   | /api/auth/login   | 1    | 200    | 340ms   |
| GET    | /api/products     | 3    | 200    | 85ms    |
| PUT    | /api/cart/{id}    | 2    | 200    | 95ms    |
```

## 零依赖

- Python 3.9+（纯标准库）
- gstack `$B` 浏览器（用于页面访问和 JS 注入）

## 文件说明

| 文件 | 作用 |
|------|------|
| `scripts/api_intercept.js` | 浏览器端注入：拦截 fetch + XHR，记录完整请求/响应 |
| `scripts/api_collect.js` | 收集已拦截的流量数据（JSON 导出） |
| `scripts/parse_network.py` | 解析 `$B network` 日志为标准 capture 格式（备选输入方式） |
| `scripts/api_rip.py` | 核心引擎：流量分析 + 去重 + 参数化 + OpenAPI + Postman + 报告 |
