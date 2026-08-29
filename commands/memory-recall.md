---
description: 从 KYM 记忆库检索相关内容
argument-hint: "<query>"
allowed-tools: "Bash(*)"
---

检索 KYM 五层金字塔记忆库并展示 top 结果。

1. 运行: !`python ${CLAUDE_PLUGIN_ROOT}/engine/cli.py recall "$ARGUMENTS"`
2. 把命中的记忆整理成简洁回答，标注置信度与层级。
