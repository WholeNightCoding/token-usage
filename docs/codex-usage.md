# Codex 统计口径

本工具只读本机保存的会话，不读账号配额，也不推算订阅账单。默认合并 Claude Code 与 Codex；来源筛选在所有图表、分析和导出入口生效。

## 读取与去重

- Claude 原有 `message.usage` / `usage` 解析保留；message ID 按字段最大值合并流式快照和子任务副本。
- Codex 从 `CODEX_HOME`（未设置时 `~/.codex`）的 `sessions`、`archived_sessions` 读取。项目取 `session_meta.cwd` / `turn_context.cwd`，模型取请求或当前 turn context；缺失时显示 unknown。
- 新格式先读 `token_usage_record.payload.usage`，按 response ID 去重。相同请求先合并包含缓存的原始输入计数，再拆分，避免不同快照的非缓存与缓存最大值重复相加。
- 相同线程累计计数对应的 `event_msg/token_count` 是镜像，跳过。没有新格式的旧日志按累计差值统计；重复快照跳过，计数重置时使用 last usage。首条旧累计记录有 last usage 时只记这次用量，不把缺失的过去历史记到当前时间。
- 时间窗前的计数也用于计算增量，再按事件时间过滤。fork 生成时间之前的继承记录使用父线程身份去重，独立子线程之后的请求各自计数。
- 损坏 JSON、未写完的行和无有效 usage 的事件跳过；不写会话文件。文件被归档/删除的读取竞争不会阻断其他文件。

## 数字含义

统一列：INPUT（非缓存输入）、OUTPUT、CACHE_READ、CACHE_CREATE、TOTAL。

Codex 的 input 已包含缓存读/写，因此拆分为剩余输入 + 缓存读 + 缓存写；reasoning 已包含在 output，仅作为额外明细。总量保持原始 input + output，避免把子集再计一次。此处同时兼容本机 Desktop 的逐请求记录和旧式累计计数格式。

Claude 计费折算和 USD 估算沿用现有配置；Codex 暂无价格配置，JSON 的 `unpriced_tokens` 明确给出未估算部分。`billing_scope=claude`，不会用 Claude 的价格代替 Codex。效率统计含 Codex 时，前端只允许 raw 视图。

模型、项目、每日 JSON 的原有字段保留；增加来源分组、reasoning output 和估价范围。CLI 的 `MSGS` 在 Codex 下表示去重后的用量事件数，不代表用户发送的消息数。

## 本地验证

```bash
python3 -m unittest discover -s tests -v
node --check dashboard/static/app.js
python3 scripts/count_tokens.py --source codex --today --json
python3 scripts/analyze.py --source codex --days 7 --html /tmp/codex-report.html
python3 scripts/work_efficiency.py 7 --source codex
```

测试使用临时真实 JSONL 和本机 HTTP 服务，覆盖两种 Codex 格式、镜像/归档/分叉去重、缓存/推理子集、部分快照、跨日期计数、计数重置、Claude 原统计兼容、来源缓存隔离以及只读约束。普通统计不调用 AI。
