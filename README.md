---
agent_load: false
---

# project-state-tracker

Kiro Agent Skill — 管理 Research / Decision / Plan / LandingPrompt / TestPrompt 工件的完整生命周期，通过中心化 `status.yaml` 追踪状态与依赖。

## 三件套协作关系

### 全局架构

```
+===========================================================================+
|                        PST 三件套 -- 全局数据流                             |
+===========================================================================+
|                                                                           |
|  +---------------------+                                                  |
|  | project-state-spec  |  Stage 1: R + D                                 |
|  |       (PSS)         |  Stage 2: Plan                                  |
|  |     [规划者]         |  Stage 3: LP[] + TP[]                           |
|  +----------+----------+                                                  |
|             |                                                             |
|             | scaffold_spec.py --> apply_changes.py                        |
|             | 转换: null --> draft (注册新工件)                             |
|             v                                                             |
|  +-------------------------------------------------------+                |
|  |            project-state-tracker (PST)                 |                |
|  |                   [管家]                                |                |
|  |                                                       |                |
|  |   status.yaml <-- 单一事实源 (Single Source of Truth)   |                |
|  |                                                       |                |
|  |   +----------+  +----------+  +----------+            |                |
|  |   | S3 INIT  |  | S4 AUDIT |  |S11 REVIEW|            |                |
|  |   | 初始化    |  | 状态推进  |  | 质量审计  |            |                |
|  |   +----------+  +----------+  +----------+            |                |
|  |                                                       |                |
|  |   tools/: scan_changes, propagate, apply_changes,     |                |
|  |           validate_status, render_status               |                |
|  |   views/: DAG, 泳道图, 状态统计, review_report         |                |
|  +----------------------------+--------------------------+                |
|                               |                                           |
|           +-------------------+-------------------+                       |
|           | 读取 status       |  回流结果          |                       |
|           | (依赖门检查)       |  (Phase B)        |                       |
|           v                   |                   |                       |
|  +----------------------------+--+                                        |
|  |  Execute-LandingPrompt       |                                         |
|  |         (ELP)                |                                         |
|  |       [执行者]                |                                         |
|  |                              |                                         |
|  |  Phase A: 执行单个 LP         |                                         |
|  |  Phase B: 回流到 status.yaml  |                                         |
|  |  Result/: 执行历史归档        |                                         |
|  +------------------------------+                                         |
|                                                                           |
+===========================================================================+
```

### 典型工作流时序

```
用户          PSS              PST              ELP
 |             |                |                |
 |--new topic->|                |                |
 |             |--R+D+Plan+LP->| apply_changes  |
 |             |  (null->draft) | 注册工件        |
 |             |<-artifact IDs--|                |
 |             |                |                |
 |------------------audit----->|                |
 |             |                |--scan_changes  |
 |             |                |--propagate     |
 |             |                |--apply (推进)   |
 |             |                |--validate      |
 |             |                |--render views  |
 |             |                |                |
 |---------------------------execute LP-------->|
 |             |                |                |--依赖门检查
 |             |                |<--read status--|  (Phase A Step 2)
 |             |                |                |
 |             |                |                |--执行 LP 任务
 |             |                |                |--验证结果
 |             |                |                |--写 Result/
 |             |                |                |
 |             |                |<-Phase B 回流--|
 |             |                |  (ready/needs_ |
 |             |                |   update/blocked)
 |             |                |                |
 |------------------review---->|                |
 |             |                |--读 Result+代码 |
 |             |                |--AC 满足度评估   |
 |             |                |--架构一致性评估  |
 |             |                |--输出评级报告    |
 |             |                |                |
```

### 状态转换权限矩阵

```
+----------+-------------------+--------------------+------------------+
|  Actor   |  允许的转换        |  机制              |  约束             |
+----------+-------------------+--------------------+------------------+
|  PSS     |  null -> draft    |  apply_changes.py  |  仅注册新工件     |
|          |  (R/D/Plan/LP/TP) |  source: PSS       |  不推进状态       |
+----------+-------------------+--------------------+------------------+
|  ELP     |  draft -> ready   |  apply_changes.py  |  仅限 LP 工件     |
|          |  ready -> ready   |  或 direct-write   |  ready 要求       |
|          |  ready -> needs_  |  source: ELP       |  依赖门 passed    |
|          |    update         |                    |                  |
|          |  ready -> blocked |                    |  forced/verify   |
|          |  needs_update ->  |                    |  -> needs_update |
|          |    ready          |                    |                  |
|          |  blocked -> ready |                    |                  |
+----------+-------------------+--------------------+------------------+
|  PST     |  所有非终态转换    |  AUDIT Step 4      |  R/D/Plan/TP     |
|  (AUDIT) |  (draft->reviewed |  agent 审核        |  confidence >=90%|
|          |   ->approved      |  approved_         |  auto-approve    |
|          |   ->ready         |  transitions.json  |  60-89% flag     |
|          |   needs_update    |                    |  <60% hold       |
|          |   blocked 等)     |                    |                  |
+----------+-------------------+--------------------+------------------+
|  用户    |  任意 (手动编辑)   |  直接改文件 ->     |  下次 AUDIT      |
|          |                   |  scan_changes 检测 |  会 reconcile    |
+----------+-------------------+--------------------+------------------+
```

### PST 作为管家的职责

```
PST 对 PSS 的服务:
  +-- 提供 status.yaml 作为 PSS 注册工件的目标
  +-- apply_changes.py 接受 PSS 的 null->draft 写入
  +-- AUDIT 自动为 PSS 新增的 LP 生成 preconditions (S6C)
  +-- AUDIT backfill PSS 写入的 pending_consumers (S6F)

PST 对 ELP 的服务:
  +-- status.yaml 提供依赖门检查的数据源
  +-- apply_changes.py 接受 ELP 的状态回流
  +-- AUDIT drain pending_writebacks (ELP Phase B 失败恢复)
  +-- S3C INIT 补全 ELP 引导的最小 workspace
  +-- S11 REVIEW 审计 ELP 执行后的落地质量

PST 对用户的服务:
  +-- render_status.py 生成可视化 views
  +-- validate_status.py 质量检查
  +-- AGENTS.md + README 自动生成
```

### 关键交互接口

| 接口 | 方向 | 数据 | 文件 |
|------|------|------|------|
| 工件注册 | PSS -> PST | `{artifact, from:null, to:draft}` | `approved_transitions.json` |
| 状态查询 | ELP -> PST | 读取 artifacts/PCs/gates/HCs/blockers | `status.yaml` |
| 执行回流 | ELP -> PST | `{artifact, from:X, to:ready/needs_update/blocked}` | `approved_transitions.json` 或 direct-write |
| HC 注册 | ELP -> PST | `{op:handoff_register, facts, constraints}` | `approved_transitions.json` |
| 失败恢复 | ELP -> PST | 入队未写入的 payload | `pending_writebacks.json` |
| PC 生成 | PST 内部 | 为 PSS 新增 LP 补充 preconditions | `status.yaml` |
| 视图渲染 | PST -> 用户 | DAG/泳道/状态统计 | `views/*` |

## 功能

- 9-state 状态机流转（draft -> reviewed -> approved -> ready -> ...）
- **INFERENCE** 核心能力：读取文档自动提取 artifact ID、依赖关系、handoff context
- **S11 REVIEW Mode** — 从 ready LP 倒推 Plan AC + 架构设计，审计落地质量
- **Group Derivation** — 依赖链聚类自动推导泳道分组
- SCL Pipeline: scan -> propagate -> review -> apply -> validate -> render
- 自动生成 views（DAG、泳道图、状态统计）和 AGENTS.md

## 运行模式

| 触发词 | 模式 | 说明 |
|--------|------|------|
| "init" | S3 INIT | 初始化项目结构 |
| "audit" | S4 AUDIT | 完整状态审计 |
| "review" / "审计质量" | S11 REVIEW | 落地质量审计 |
| "regenerate views" | render | 仅刷新视图 |

## 安装

将本文件夹放置于 `~/.kiro/skills/project-state-tracker/`。

## 许可证

MIT
