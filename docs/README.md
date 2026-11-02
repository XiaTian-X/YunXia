# 任务导航

本页供维护者和 Agent 选择工作流程。仓库级入口为 [AGENTS.md](../AGENTS.md)；不自动加载该文件的客户端、或从仓库外发起的任务，应先显式读取它。环境准备见 [开发、构建与安装](development.md)。

协议维护先阅读 [系列原则](principles.md)，再按下表读取所需章节。协议专属的设计、审阅分析和采用说明位于 `agreements/<协议ID>/`。

| 要完成的任务 | 详细流程 | 预期交付 |
| --- | --- | --- |
| 新增协议并起草 | [设立与起草](maintenance.md#create) | 稳定 ID、设立目的、工作正文及配套资料 |
| 审阅当前材料 | [审阅与冻结](maintenance.md#review) | 绑定具体材料的结论、依据和未决项 |
| 固定候选版本 | [审阅与冻结](maintenance.md#review) | 可解析的候选登记、源码提交和标签 |
| 发布到本地目录 | [发布决定与本地交付](maintenance.md#local-publication) | 经核对的本地交付及完成回执 |
| 发布到 GitHub | [GitHub 发布与恢复](maintenance.md#github-publication) | 公开附件核验及已推送的完成回执 |
| 更新协议版本 | [版本修订](maintenance.md#revision) | 新目标版本与明确基线，随后进入审阅流程 |
| 推荐、暂停、停止维护或撤回 | [维护状态](maintenance.md#maintenance-state) | 对应决定与状态记录，保留固定版本 |
| 处理锁、发布中断或安装失败 | [异常与恢复](maintenance.md#recovery) | 根据失败阶段恢复一致状态并重新核验 |
| 开发工具、接入适配或安装 Skill | [开发、构建与安装](development.md) | 对应测试、构建或安装结果 |
| 为具体项目采用协议 | [yunxia Skill](../skills/yunxia/SKILL.md) · [项目工作流](../skills/yunxia/references/workflow.md) | 项目的确切版本绑定、范围声明及交付检查 |
| 恢复具体项目的未完成写入 | [项目事务恢复](../skills/yunxia/references/recovery.md) | 指定采用单元恢复到操作前状态 |

每项维护流程在对应章节说明输入和完成条件。候选冻结、发布意图、本地交付和远程发布是不同结果；最终报告应与实际完成的阶段一致。

协议规则由 `principles.md` 解释，维护步骤由 `maintenance.md` 说明，执行校验由现有工具负责；项目采用步骤只在 Skill 文档维护。仓库当前状态见 [项目首页](../README.md)，自身来源与范围见 [项目说明](project-rights.md)。
