# 任务导航

本页供维护者和 Agent 选择流程。先读取 [AGENTS.md](../AGENTS.md)，环境准备见 [开发、构建与安装](development.md)，仓库分工见 [架构与固定来源](architecture.md)。协议仓库默认位于 `repositories/YunXia-Agreements/`，与本仓库分别提交和推送。

维护协议时先读取目标仓库的 `AGENTS.md`、`docs/principles.md` 及 `agreements/<协议ID>/` 中的相关资料。通用操作步骤只在本维护指南保存；协议仓库通过固定工具版本选择对应指南。

| 要完成的任务 | 详细流程 | 预期交付 |
| --- | --- | --- |
| 准备协议仓库指定的工具 | [固定工具链准备](development.md#toolchain) | 可信来源、确切提交及经过核对的执行目录 |
| 从想法立项并起草 | [设立与起草](maintenance.md#create) | 明确目标、条款取舍、稳定 ID、草案及待核项 |
| 修改正文、说明或名称 | [修改工作材料](maintenance.md#edit) | 变更依据、影响范围及相应审阅更新 |
| 审阅条款和当前材料 | [审阅与冻结](maintenance.md#review) | 有依据的分析、真实决定及未决项 |
| 固定候选版本 | [审阅与冻结](maintenance.md#review) | 可解析的候选登记、源码提交和标签 |
| 发布到本地目录 | [发布决定与本地交付](maintenance.md#local-publication) | 经核对的交付及完成回执 |
| 发布到 GitHub | [GitHub 发布与恢复](maintenance.md#github-publication) | 公开附件核验及已推送的完成回执 |
| 更新协议版本 | [版本修订](maintenance.md#revision) | 新目标版本与固定基线，随后进入审阅 |
| 推荐、暂停、停止维护或撤回 | [维护状态](maintenance.md#maintenance-state) | 实际决定和状态记录，保留固定版本 |
| 提交并推送维护结果 | [Git 阶段约定](maintenance.md#git) | 对应阶段的可核验提交和远端状态 |
| 接续其他会话或中断的任务 | [继续维护](maintenance.md#resume) | 重建当前事实、授权范围和下一步 |
| 处理锁、发布中断或安装失败 | [异常与恢复](maintenance.md#recovery) | 根据真实阶段恢复一致状态并核验 |
| 更新协议来源或开发、构建、安装 Skill | [开发、构建与安装](development.md) | 固定输入、测试及独立包结果 |
| 为具体项目采用协议 | [yunxia Skill](../skills/yunxia/SKILL.md) · [项目工作流](../skills/yunxia/references/workflow.md) | 确切版本绑定、范围声明及交付检查 |
| 恢复项目的未完成写入 | [项目事务恢复](../skills/yunxia/references/recovery.md) | 指定采用单元恢复并重新核验 |

阶段输入和完成条件见各流程。候选冻结、发布意图、交付、公开发布及回执同步是不同结果；最终报告必须对应实际状态。工作记录复用协议的设计和审阅文件及 registry，不再维护第二份进度数据库。

协议系列定位和实质原则由 [协议仓库](https://github.com/XiaTian-X/YunXia-Agreements) 维护，执行校验由本工具负责，项目采用步骤只在 Skill 文档维护。本仓库当前状态见 [项目首页](../README.md)，来源与范围见 [项目说明](project-rights.md)。
