# 仪表盘统计索引

`dashboard/server.py` 用 `scripts/transcript_index.py` 复用逐文件解析结果。刷新时先检查文件属性，只重新解析有变化的文件；之后复用原解析器进行跨文件去重和统计。文件列表与统计结果形成一次请求的快照，并发请求共享进行中的扫描。

SQLite 保存压缩后的事件计数，不保存会话正文。默认位置：

- macOS：`~/Library/Caches/token-usage/<来源目录摘要>/transcripts.sqlite3`
- Windows：`%LOCALAPPDATA%/token-usage/<来源目录摘要>/transcripts.sqlite3`
- Linux：`$XDG_CACHE_HOME/token-usage/<来源目录摘要>/transcripts.sqlite3`，未设置时使用 `~/.cache`。

来源目录不同的默认索引互相隔离。`--cache-dir PATH` 可指定索引目录；`--no-cache` 关闭磁盘索引，保留内存缓存。日志仍为只读。首次扫描、解析器源码变化、缓存丢失需要重建；普通刷新和服务器重启复用索引。缓存不可写或整个数据库损坏会告警并回退到内存；损坏的单条缓存自动重新解析对应文件。

## 本机对比

约 1,997 个文件、4.54 GiB 日志，包含持续写入的 Codex 会话。比较同一个 Codex 今日仪表盘查询：

| 场景 | 耗时 |
|---|---:|
| 优化前，日志变化后的三次查询 | 12.97 / 13.82 / 15.94 秒 |
| 首次建立索引 | 8.47 秒 |
| 关闭并重新创建索引连接后读取 | 0.73 秒 |
| 后续查询 | 0.20 / 0.01 秒 |

以上为本机服务端实测，页面绘图和网络另计；文件数量及用量会随工作增长。首次建立索引不属于已缓存的加载速度。

## 验证

```bash
python3 -m unittest discover -s tests -v
node --check dashboard/static/app.js
python3 dashboard/server.py --no-open --no-reload
```

测试覆盖真实 SQLite 的关闭/重开、只重新读取变化文件、归档副本去重、删除、部分行补齐、同大小改写、缓存校验、解析器版本更新与存储不可用回退；同时让 Claude/Codex 原有计数用例通过索引路径再跑一遍。

决策背景见 [ADR 001](decisions/001-transcript-index.md)。
