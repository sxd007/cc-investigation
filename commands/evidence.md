---
description: 证据管理 — 链式保管、证据清单、证据评估
---

# /evidence

调查证据全生命周期管理。

## Usage
```
/evidence list [case#]      查看证据清单
/evidence add               添加证据记录
/evidence chain [evidence#] 查看/更新保管链
/evidence assess [evidence#] 证据可采性评估
/evidence integrity [case#]  运行证据链完整性检查
```

## Process

所有操作遵循 evidence-management 技能的 ALCOA 原则和 SPIRIT 评估框架。

### /evidence add

添加新证据时，需同时：

1. 在 `evidence_registry.json` 的 `evidence_items[]` 中注册核心信息
2. 创建 `nodes/EV-NNN.md` 文件（详细分析和描述）
3. 在 `evidence_registry.json` 的 `chain_nodes[]` 中追加索引
4. 追加 `CHANGELOG.json` 变更记录

### /evidence integrity

运行 `skills/evidence-management/scripts/scan-chain.py --integrity` 检查：

- 所有 finding 的 sources 链是否完整
- 是否有 draft 节点阻塞 ready finding
- 是否有孤立节点或缺失引用
