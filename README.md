# cc-investigation 🔍

**反舞弊调查全流程插件市场** — 调查方法论、证据链管理、访谈分析、可视化报告、审计技术、流程分析等专业技能的 Claude Code 插件市场。

## 概述

cc-investigation 是专为反舞弊调查人员和内部审计师设计的 Claude Code 插件市场，提供从报案受理到结案归档的全流程专业支持。

## 安装

```bash
# 添加市场源
claude plugin marketplace add https://github.com/sxd007/cc-investigation

# 安装（全量）
claude plugin install cc-investigation@cc-investigation

# 安装（按配置）
claude plugin install cc-investigation@cc-investigation --profile investigator
claude plugin install cc-investigation@cc-investigation --profile auditor
```

## 安装配置

| 配置 | 适用人群 | 包含模块 |
|------|---------|---------|
| `minimal` | 所有用户 | 核心命令 + 规则 + 代理 |
| `investigator` | 一线调查员 | 全流程技能（默认） |
| `auditor` | 内部审计师 | 侧重审计 + 数据分析 |
| `analyst` | 数据分析师 | 侧重数据挖掘 + 可视化 |
| `interviewer` | 访谈专家 | 侧重访谈 + 陈述分析 |
| `full` | 全都要 | 所有技能模块 |

## 技能体系

```
调查哲学与方法论 (认知基础)
  ├── 舞弊类型与调查方案 (领域知识)
  ├── 调查工具与技术 (方法论)
  ├── 访谈与问话分析 (沟通技能)
  ├── 证据链与底稿管理 (证据技能)
  ├── 数据分析与审计技术 (技术技能)
  ├── 写作与报告技巧 (输出技能)
  └── 调查流程与案件管理 (管理技能)
```

## 命令

- `/investigate` — 调查案件管理（立案→计划→跟踪→结案）
- `/evidence` — 证据管理（添加→保管链→评估）
- `/interview` — 访谈策划与分析（策划→问话→分析→笔录）
- `/report` — 文书撰写（底稿→备忘录→报告→可视化）
- `/analyze` — 数据分析（异常检测→趋势→关联）
- `/fraud-type` — 舞弊类型识别与方案推荐
- `/working-paper` — 底稿管理（创建→索引→复核）
- `/case` — 案件总览仪表盘

## 代理

| 代理 | 用途 |
|------|------|
| `investigation-planner` | 调查方案设计 |
| `evidence-analyzer` | 证据评估 |
| `interview-analyzer` | 访谈陈述分析 |
| `report-writer` | 报告撰写 |
| `fraud-type-classifier` | 舞弊类型分类 |
| `data-analyzer` | 数据分析 |

## 项目结构

```
cc-investigation/
├── .claude-plugin/
│   ├── plugin.json              # 插件清单
│   ├── marketplace.json         # 市场元信息
│   └── PLUGIN_SCHEMA_NOTES.md   # Schema踩坑记录
├── manifests/
│   ├── install-modules.json     # 模块定义
│   ├── install-components.json  # 组件定义
│   └── install-profiles.json    # 安装配置
├── skills/                      # 技能定义 (8个领域)
├── commands/                    # 斜杠命令 (8个)
├── agents/                      # 子代理定义 (6个)
├── rules/                       # 调查准则 (4个)
├── hooks/                       # 生命周期钩子
├── docs/                        # 文档
├── CLAUDE.md                    # 项目指导
└── README.md
```

## 持续扩展

本插件市场采用**核心 + 横向扩展**策略：
1. **Phase 1 (当前):** 核心框架 + 8个通用技能
2. **Phase 2:** 各舞弊类型深度技能（按行业/按手法细分）
3. **Phase 3:** 特定行业调查方案（金融/医药/科技/制造）
4. **Phase 4:** 调查辅助工具集成（MCP 服务器、外部工具对接）

## 作者

**Alpha Shen** — 反舞弊调查从业者

- GitHub: [sxd007](https://github.com/sxd007)
- 项目主页: [github.com/sxd007/cc-investigation](https://github.com/sxd007/cc-investigation)

本项目由作者独立开发维护。如有问题、建议或合作意向，欢迎通过 GitHub Issues 联系。

## ⚠️ 重要免责声明

**本插件为反舞弊调查方法论知识工具集，不构成法律意见或专业调查建议。**

- **本插件是辅助性工具，不是替代性决策系统**。AI 帮助探索、分析和批判，但所有输出均受调查员的风格、经验和提问方向的影响。调查员应始终保持独立审慎的态度，仔细评估 AI 输出，做出独立决策
- AI 生成的内容可能存在幻觉、偏见或过时信息，使用者有义务独立验证任何结论
- 连接器（MCP）仅作为能力目录列出，用户自行配置并对其使用行为**承担全部法律责任**
- 严禁将本插件用于非法监控、未经授权的数据采集、歧视性筛选或任何违反适用法律的活动
- 使用前请务必阅读完整的 [DISCLAIMER.md](DISCLAIMER.md)（免责声明与使用条款）和 [rules/investigation-ethics.md](rules/investigation-ethics.md)（AI 辅助调查道德准则）

**任何不经过独立思考和专业判断、完全依赖 AI 生成结果而导致的误判、漏判或错误决策，本插件及其作者概不负责。下载或使用本项目的任何部分，即表示您已阅读、理解并同意受免责声明约束。**

---

## License

Copyright 2026 Alpha Shen

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

See the [LICENSE](LICENSE) file for details.
