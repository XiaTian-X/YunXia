# 开发、构建与安装

任务入口见 [任务导航](README.md)，输入边界见 [架构与固定来源](architecture.md)。协议设立、审阅、发布和修订按 [维护指南](maintenance.md) 执行；本页维护工具开发、来源更新、适配测试和分发约定。

## 环境与检查

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r tooling/requirements-dev.txt
make test
python3 -B tooling/yunxia.py source fetch --id YunXia-Agreements
make check
```

Python 核心使用标准库，完整测试的 JSON Schema 契约检查使用 jsonschema。维护和构建需要 Git；GitHub 发布另需已认证的 gh。写入和恢复需要 macOS/Linux 等支持 POSIX 文件锁的系统；不支持时明确报错，只读查询不需要该锁。

`make test` 运行使用独立临时协议仓库的基础回归，不要求本工具仓库保留协议正文。`make check` 同时核验工具、文档、来源锁与实际协议集成；源缺失不能静默跳过。协议仓库单独检查：

```bash
python3 -B tooling/yunxia.py check --repository repositories/YunXia-Agreements
```

协议检查不要求目标目录存在工具源码或测试目录。不带 `--repository` 的 `check` 默认检查当前工具仓库；显式给出该参数时默认检查协议，如需从其他目录检查工具仓库，同时传入 `--scope tool`。检查不自动刷新摘要、改变协议身份或创建批准；测试中的发布只针对隔离夹具和模拟远端，不发布实际协议。

## 来源更新

`sources.lock.json` 保存受信任的仓库地址、完整目录提交和所辖协议 ID。本地独立仓库位于 `repositories/<来源ID>/`，由父仓忽略，不能作为缓存删除。`source fetch` 显式获取来源并核验锁定提交。首次获取会建立独立克隆，工作树位于远端默认分支；已有目录只更新远端引用和对象，不切换或覆盖本地分支及未提交修改。返回的 `working_head`、`working_branch` 描述维护工作树，`commit` 描述构建使用的固定提交；两者可以不同。`fetch` 不改来源锁，构建自身不访问最新分支或替换工作树。子仓缺失、Git 根不匹配或固定对象缺失时应修复实际来源，不能回退父仓或用工作正文顶替。

需要纳入协议新版本、推荐或撤回变化时，先在协议仓库完成对应维护及必要的提交推送，核对确切目录提交，然后显式更新锁：

```bash
python3 -B tooling/yunxia.py source fetch --id YunXia-Agreements
python3 -B tooling/yunxia.py source pin --id YunXia-Agreements --commit FULL_COMMIT
```

`FULL_COMMIT` 替换为已核对的完整提交，不用分支名或 latest。`pin` 校验后才更新锁，只接受原固定提交的后续历史，拒绝倒退或无关历史。检查返回的新增版本、状态和推荐变化，以及锁文件差异和适配影响，再运行相关测试和 `make check`。来源更新不自动新增适配，不修改旧项目的采用引用，也不创建协议发布。正式版本的目录快照应包含完成回执；历史草案保持原草案身份。

## 适配与构建

适配规则及支持登记保留在 `tooling/src/yunxia/adapters/`。新增支持先确认既有工作流适合协议的实际要求，再登记确切引用并补充真实输入/输出测试：

```bash
python3 -B tooling/yunxia.py adapter --agreement AGREEMENT --version VERSION --output-name LICENSE.example-1.txt
make build
```

元数据不能注入可执行代码。其他工作流须实现和验证后接入；收录或登记支持不改变协议发布状态。协议版本、工具版本、构建目录快照和项目状态格式分别管理；项目状态格式仍为 3，三字段引用不变。

构建读取工具输入和来源锁指定提交中的协议目录，固定正文继续按 registry 的提交、路径和摘要提取。协议工作树的未提交内容不进入包。`dist/skills/yunxia/BUILD.json` 记录分区输入与包内文件摘要；复制结果逐项核对，输入并发变化、缺少资产、额外文件、符号链接或摘要不符不能替换可用构建。

源码入口是 `tooling/yunxia.py`。`sources` 处理固定来源，`catalog` 读取协议事实，`gitstore` 提取原始 Git 对象，`lifecycle` 管理协议阶段，发布模块处理意图与回执，`project` 处理采用项目，`distribution` 组合和验证独立包，`common` 提供共享校验和原子写入。协议历史中的代码不执行。不要在协议库复制工具或第二份适配登记。

## 安装与恢复

```bash
make skill-install
```

`skills/yunxia/` 是指令源码，安装使用构建包。包内携带引擎、目录快照和原字节资产，离开两个源码仓库、没有 Git 或网络仍可运行。离线目录只代表构建时状态，不宣称已获知后来的推荐或撤回。

安装前核对构建包与当前固定来源。目标必须在工具源码之外，不能是源码的上级目录；已有非空目录必须确实是 yunxia Skill。替换时只临时暂存旧目录以便完成当前事务，成功后清理，不创建长期 `skill-backups`。失败时根据实际错误路径核对并恢复，仅保留恢复所需现场；不删除已有的历史备份，也不以新备份掩盖未恢复状态。包内摘要核验一致性，来源信任依靠已确认的来源和安装渠道。

维护锁使用 `.yunxia-maintenance.guard`，项目使用 `.yunxia/lock.guard` 作为保留的进程互斥文件；这些忽略文件不是应当删除的缓存。PID 记录在完整写入后原子创建，恢复持有相同进程锁，损坏记录保留并报错。具体恢复入口见 [异常与恢复](maintenance.md#recovery)。

不生成备份用途的全仓副本、压缩包、Git bundle 或长期安装副本。环境、缓存、发布暂存、构建产物、私有项目状态和检查报告由 `.gitignore` 排除。两个仓库分别检查和提交，父仓提交前核对 `repositories/` 下没有被跟踪的文件或 gitlink。
