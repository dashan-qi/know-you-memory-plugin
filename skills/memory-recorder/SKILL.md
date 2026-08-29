---
name: memory-recorder
description: 什么时候把信息写进 KYM 记忆库、什么时候检索记忆。Use when 用户透露偏好/决策/身份信息、完成关键操作、踩坑、或问"上次/之前/你记得/你说过"等涉及历史的问题。
---

# KYM 记忆记录

KYM 是五层金字塔持久记忆，纯本地零依赖。数据在 `~/.memory_core/memory.db`。

## 什么时候 ADD（写入记忆）
- 用户透露新偏好、决策、身份信息 → 立即写
- 完成关键操作/修复 → 记录结论与踩坑
- 跨会话需要记住的事实

调用 MCP 工具 `mcp__plugin_know-you-memory_kym__memory_add`
（参数: content 必填, layer/tags/project 可选）。
无 MCP 时用 `!python ${CLAUDE_PLUGIN_ROOT}/engine/cli.py add <内容>`。

## 什么时候 RECALL（检索记忆）
- 用户问"上次/之前/你记得/你说过/之前怎么修的"
- 涉及历史项目状态、要避免重复踩坑

调用 `mcp__plugin_know-you-memory_kym__memory_recall`
或无 MCP 时 `!python ${CLAUDE_PLUGIN_ROOT}/engine/cli.py recall <query>`。

## 原则
- 内容要自包含：让未来的自己无需上下文也能读懂
- 不记临时细节、不记敏感凭据
- L1/L2/L3（原则/画像/偏好）写入要慎重，凭内容判断
