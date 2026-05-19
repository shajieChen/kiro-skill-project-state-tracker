# project-state-tracker

Kiro Agent Skill — 管理 Research / Decision / Plan / LandingPrompt / TestPrompt 工件的完整生命周期，通过中心化 `status.yaml` 追踪状态与依赖。

## 功能

- 9-state 状态机流转（draft → reviewed → approved → ready → …）
- **INFERENCE** 核心能力：读取文档自动提取 artifact ID、依赖关系、handoff context
- SCL Pipeline: dirty_check → scan → propagate → review → apply → render
- 确定性质量验证（quality_check.py, 10 条规则）
- 自动生成 views（DAG、泳道图、状态统计）和 AGENTS.md

## 运行模式

| 条件 | 模式 |
|------|------|
| 无 status.yaml + 有文档 | INIT_FROM_DOCS |
| 无 status.yaml + 无文档 | INIT_EMPTY |
| 有 status.yaml | AUDIT |

## 目录结构

```
project-state-tracker/
├── SKILL.md       # 核心 Prompt (~1200 words) — SCL 合约
├── templates/     # 项目初始化模板
└── tools/         # Python 工具链（scan、propagate、apply、render、validate）
```

## 调用方式

```text
"init"              — 初始化项目
"audit"             — 完整审计
"regenerate views"  — 仅刷新视图
"rebuild graph"     — 重建依赖图
```

## 安装

将本文件夹放置于 `~/.kiro/skills/project-state-tracker/`。

## 许可证

MIT
