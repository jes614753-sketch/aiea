# AIEA — AI Efficiency Auditor for Claude Code

Claude Code 的 Token 浪费诊断工具。不是告诉你"花了多少 token"，而是告诉你**为什么花这么多、浪费在哪、怎么降下来**。

## 安装

```bash
git clone https://github.com/jes614753-sketch/aiea.git
cd aiea
pip install -e .
```

零依赖，纯标准库。

## 使用

```bash
# 扫描默认 telemetry 目录
python -m aiea scan

# 指定目录
python -m aiea scan --dir ~/.claude/telemetry

# 只查某个 session
python -m aiea scan --session <session_id_prefix>

# 输出 Markdown 报告
python -m aiea scan --output report.md
```

## 检测的浪费模式

| 嗅探器 | 检测内容 | 严重等级 |
|--------|---------|---------|
| `toxic_file` | lockfile、编译产物、二进制文件被读入上下文 | high/medium |
| `bloated_context` | Bash 命令输出过大 (>5KB/20KB/100KB) | critical/high/medium |
| `death_loop` | 连续失败的相似命令重试 | high/medium |

## 工作原理

读取 `~/.claude/telemetry/` 下的 JSONL 事件日志，解码 base64 元数据，构建 session 时间线，然后运行嗅探器检测浪费模式。

```
telemetry JSONL → 解码 → SessionTimeline → Sniffers → WasteFinding → Report
```

## 数据来源

Claude Code 的 telemetry 事件（存储在 `~/.claude/telemetry/1p_failed_events.*.json`），包含：
- `tengu_api_success` — API 调用详情（tokens、成本、模型）
- `tengu_tool_use_success` — 工具调用详情（大小、耗时）
- `tengu_context_size` — 上下文大小快照
- `tengu_file_read_reread` — 文件重复读取事件

## License

MIT
