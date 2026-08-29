---
description: 手动分拣已有记忆文件/目录进 KYM 记忆库
argument-hint: "[path]"
allowed-tools: "Bash(*)"
---

手动把指定目录或文件的已有记忆分拣进 KYM 五层金字塔。
无参数时扫描当前 workspace（CLAUDE.md/.claude/README）。

1. 运行: !`python ${CLAUDE_PLUGIN_ROOT}/engine/cli.py import ${1:-.}`
2. 将输出（imported/by_layer）汇报给用户，说明分拣了哪些层级各几条。
