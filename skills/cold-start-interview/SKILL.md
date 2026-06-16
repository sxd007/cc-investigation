---
name: cold-start-interview
description: 首次设置向导 — 引导调查员完成团队配置、证据策略、集成检查和偏好设置。写入持久化配置路径，所有技能依赖此配置运行。支持中断恢复、升级合并和增量更新。
origin: cc-investigation
user-invocable: true
---

# Cold-Start Interview

首次安装 cc-investigation 后的设置向导。在一次对话中完成所有配置，让插件从"通用模板"变成"你的调查工具"。

---

## 触发条件

| 场景 | 行为 |
|------|------|
| 插件安装后首次触发（SessionStart hook） | 自动提示运行 |
| 用户主动运行 `/cc-investigation:cold-start-interview` | 进入状态检测 |
| 技能检测到 team-profile.md 不存在或含 `[PLACEHOLDER]` | 引导用户运行此命令 |
| 用户运行 `--check-integrations` | 仅检查集成状态，不重新访谈 |
| 用户运行 `--redo` | 重新完整访谈，覆盖现有配置（先展示 diff） |

---

## 状态检测（入口）

每次运行时，先检查配置路径的四种状态，然后分支：

```
~/.claude/plugins/config/cc-investigation/team-profile.md 的状态:

DOES_NOT_EXIST      → 进入 Phase 1（全新设置）
├── evidence-policy.md 也不存在 → 标记为"可选，稍后设置"

PAUSED              → 提示用户"上次中断于[章节]，是否继续？"
├── 用户选择继续 → 跳转至中断点
├── 用户选择重来 → 进入 Phase 1

HAS_PLACEHOLDER     → 提示"配置不完整，继续补全或重来？"
├── 用户选择补全 → 逐个定位 [PLACEHOLDER] 位置并引导填写
├── 用户选择重来 → 进入 Phase 1

READY               → "配置已就绪，是否需要：
                         ├── --redo    重新完整设置
                         ├── --check-integrations  仅检查集成
                         └── 取消"
```

团队配置已就绪（READY）且用户选了 `--redo` 时，在覆盖前展示 diff：

```
── 准备覆盖现有配置 ─────────────────────────
原值: 普通案件周期上限: 30天
新值: 普通案件周期上限: 45天
                                              ← 后续字段变化逐行展示
── 确认覆盖？(yes/no) ───────────────────────
```

---

## Phase 1: 全新设置

用户首次使用或选择重来时进入此阶段。目标：完整填写 team-profile.md。

### 1.1 组织信息

**影响技能：** case-management、report-writer

采集：组织名称、行业、主要法域、监管机构。

提问策略：先问是否有现成的介绍（公司官网/About页面/企业信息卡片），有则读取提取，无则逐项填空。**2 个问题以内完成本节。**

### 1.2 调查团队

**影响技能：** case-management、investigation-planner

团队名称、规模、负责人、汇报线。

### 1.3 调查通信纪律

**影响技能：** interview-analysis、investigation-planner

关键参数：

| 参数 | 建议提问方式 |
|------|------------|
| 同一事项连续通话上限 | "同一个举报线索，最多可以连续联系举报人几次必须停下来复盘？" |
| 通话前背景核查 | "联系举报人之前是否需要先做背景核查？" |
| 通话后强制 call_memo | "每次通话后是否强制输出通话备忘录？" |
| 案件周期上限 | "普通案件从立案到结案，通常希望控制在多少天内？" |

### 1.4 审批流程

**影响技能：** case-management、report-writer

逐项确认：立案/数据提取/访谈/报告发布是否需要审批、谁来批。

### 1.5 报告偏好

语言、报告格式、编号规则等。

---

## Phase 2: 中断恢复

当检测到 `<!-- SETUP PAUSED AT: 章节名 -->` 标记时，读取标记指向的章节，从该处继续。前置已填内容不动。

中断保存时机：
- 用户说 "暂停" / "先到这里" / "pause"
- 此时在已填完的最后一个章节末尾写入 `<!-- SETUP PAUSED AT: 下一章节名 -->`
- 未填字段标记为 `[PENDING]`（区别于模板的 `[PLACEHOLDER]`）

恢复时的对话示例：
```
> 欢迎回来。上次你在"审批流程"设置完成后暂停了。
> 你的组织信息和团队配置已经保存。

> 上次已完成的:
> ✅ 组织信息
> ✅ 调查团队
> ✅ 调查通信纪律
> ⏸ 审批流程 — 已设置，未中断在此
> □ 报告偏好 — 未开始

> 从"报告偏好"继续？
```

---

## Phase 3: 验证集成

与当前行为一致——自动检测 MCP 服务器，按类型验证可用性。运行 `--check-integrations` 时仅执行此阶段。

输出示例：
```
文件系统操作类 MCP ... ✓
推理辅助类 MCP ... ✓
网络搜索类 MCP ... ✗ (未配置)
  → 建议: 如涉及 OSINT 调查，推荐启用搜索类 MCP
表格数据处理类 MCP ... ✗ (未安装)
  → 建议: 如需 Benford 定律分析，pip install 对应包
```

---

## Phase 4: 写入配置 + 模板合并

### 4.1 全新写入

首次设置时，按模板结构写入 `~/.claude/plugins/config/cc-investigation/team-profile.md`。模板中所有 `[PLACEHOLDER]` 替换为用户填写值。

### 4.2 模板合并（升级场景）

插件升级后，`config-templates/team-profile.md` 可能新增了配置项。检测流程：

```
1. READ 新模板 (config-templates/team-profile.md)
2. READ 现有用户配置 (~/.claude/plugins/config/cc-investigation/team-profile.md)
3. 按 H2 节逐节对比:
   - 模板中存在但配置中不存在的节 → 标记为"新配置项"
   - 模板和配置中都存在但配置中有 [PLACEHOLDER] → 标记为"待补全"
   - 配置中已填写的字段 → 保留不动
4. 如存在"新配置项"或"待补全"项:
   → "插件升级后新增了以下配置项，需要补充："
   → 逐项引导填写
5. 无新增项 → "无需变更，配置已是最新"
```

### 4.3 输出摘要

```
╔══════════════════════════════════════════════════════════════════╗
║              cc-investigation 配置完成                           ║
╠══════════════════════════════════════════════════════════════════╣
║  组织: XX 集团公司  |  行业: 制造  |  法域: 中国大陆              ║
║  团队: 调查部 (5人)  |  汇报: 审计委员会                          ║
║  通信纪律: 已配置    |  审批流程: 已配置                           ║
║  集成: 3/5 可用      |  缺失: 财务分析, 企业信息查询              ║
╚══════════════════════════════════════════════════════════════════╝
┌─ 推荐的下一步 ──────────────────────────────────────────────────┐
│  /cc-investigation:investigate start    启动第一个案件           │
│  /cc-investigation:cold-start-interview  重新配置               │
│  --check-integrations                    检查集成状态            │
└────────────────────────────────────────────────────────────────┘
```

---

## 升级说明（发版注意）

每次发版更新 `config-templates/team-profile.md` 时：

1. **保留模板结构**：所有字段保持 `[PLACEHOLDER]` 标记
2. **新增字段必须标注"影响技能"**：确保 cold-start-interview 能识别并归入正确的合并流程
3. **不修改 config-loader.md**：该契约已覆盖所有场景
4. **用户配置不受影响**：`~/.claude/plugins/config/cc-investigation/*` 不会被插件更新覆盖

---

## Related

- **Skills:** [调查流程与案件管理](../case-management/SKILL.md)、[MCP 能力目录](../mcp-integration/SKILL.md)
- **Config System:** [config-loader.md](../../config-templates/config-loader.md)、[team-profile.md 模板](../../config-templates/team-profile.md)
- **Commands:** `/cc-investigation:cold-start-interview`, `--check-integrations`, `--redo`
