# API-Rip — API 逆向抓取器

> 打开网站，偷走完整 API 文档

浏览任意网站，自动拦截所有 API 请求，去重归类后生成 OpenAPI 3.0 + Postman Collection。

## 快速使用

在 Claude Code 中直接说：
```
帮我抓取 https://example.com 的 API
```

或命令行：
```bash
# 1. 浏览网站并捕获网络流量
$B network --clear
$B goto https://example.com && $B wait --networkidle
$B network > /tmp/network.txt

# 2. 解析 + 生成文档
python3 scripts/parse_network.py -i /tmp/network.txt -o /tmp/captures.json
python3 scripts/api_rip.py -i /tmp/captures.json --all /tmp/api_rip
```

生成三个文件：
- `*_openapi.json` — 导入 Swagger Editor
- `*_postman.json` — 导入 Postman
- `*_report.md` — 人可读的 API 文档

## 核心能力

- 拦截所有 fetch + XHR 请求（JS 注入模式）
- 解析浏览器网络日志（网络日志模式，更可靠）
- 自动过滤静态资源和分析脚本
- 路径参数化：`/users/123` → `/users/{id}`
- UUID/ObjectId/Token 自动识别
- 从响应推断 JSON Schema
- 去重归类同一模式的请求
- 生成标准 OpenAPI 3.0.3
- 生成 Postman Collection v2.1（含文件夹分组）
- 人可读的 Markdown API 文档

## 两种捕获模式

| 模式 | 优点 | 缺点 |
|------|------|------|
| JS 拦截器 | 获取完整请求/响应 body 和 headers | 页面跳转会重置 |
| 网络日志 | 不会被重置，捕获所有页面 | 无法获取 body 内容 |

建议：先用网络日志模式发现端点，再用 JS 模式获取详细数据。

## 零依赖

- Python 3.9+（纯标准库）
- gstack `$B` 浏览器
