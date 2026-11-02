<p align="center">
  <img src="https://raw.githubusercontent.com/XiaTian-X/xia-logo/main/yunxia/latest/yunxia-primary-latest.svg" alt="允夏 Yǔn Xià" width="360">
</p>

<h1 align="center">允夏 YǔnXià</h1>

<p align="center">
  <strong>管理协议全周期，通过 Skill 帮助具体项目采用确切协议。</strong>
</p>

<p align="center">
  <a href="#当前状态">当前状态</a> ·
  <a href="#项目能力">项目能力</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#目录结构">目录结构</a> ·
  <a href="#文档导航">文档导航</a>
</p>

---

允夏（拼音：yǔn xià）提供协议从立项、起草、审阅到发布、更新和停止维护的管理工具，并通过 **yunxia Skill** 帮助具体项目采用指定协议及确切版本。

本仓库开发工具、数据契约、适配器和 Skill。正文、审阅、版本登记及发布事实由独立的 [YunXia-Agreements 协议仓库](https://github.com/XiaTian-X/YunXia-Agreements) 维护；两个仓库分别提交和推送，协议变化不与工具代码变化混在同一条日常提交历史中。

## 当前状态

| 项目 | 状态 |
| --- | --- |
| 维护工具与 Skill | 0.6.0 |
| 协议仓库默认位置 | `repositories/YunXia-Agreements/`，父仓整体忽略 |
| 构建协议来源 | [sources.lock.json](sources.lock.json) 固定仓库与完整目录提交 |
| 已收录协议 | 允夏许可证，当前工作正文为 0.9.3 审阅草案 |
| 固定历史 | 三份 imported-draft，原登记及正文摘要保持 |
| 正式协议发布 | 暂无 |
| 项目采用状态 | 格式 3，使用协议 ID、版本、SHA-256 三字段引用 |
| 运行环境 | Python 3.11+；写入与恢复需要支持 POSIX 文件锁的系统 |

协议仓库的 registry 是固定版本与发布事实的唯一维护位置。历史正文从确切 Git 对象读取，迁移保留旧引用所需的固定历史来源分支，不在当前工作树复制历版正文。工具更新不会改变项目已采用的协议。

## 项目能力

### 协议全周期维护

Agent 可以从初步目标开始梳理立项、比较已有协议、起草条款和典型场景，依据实际决定修改并审阅材料。工具将审阅绑定到确切文件集合，形成固定候选，再按发布决定完成本地交付或 GitHub Release、附件核验和回执同步。

版本更新从明确基线开始；推荐、暂停、停止维护和撤回分别保存决定，旧固定版本仍可定位。发布中断根据已有结果恢复，不删除已公开事实伪装回滚。检查、提交、工具安装和发布是不同阶段，Agent 在已有授权范围内持续完成，不反复确认已决定的事项。

从 [Agent 工作入口](AGENTS.md) 和 [任务导航](docs/README.md) 开始；协议实质原则和专属材料随协议仓库维护，通用操作流程只在本仓库保存。

### 具体项目采用

`yunxia` Skill 只负责目标项目：选择确切协议，扫描内容和已有许可，复用已确认事实并补问缺口，按原字节提供协议正文，生成范围声明及必要配套材料，最后增量核验实际目录或 ZIP 交付物。

同一项目可以按代码、文档等范围建立不同采用单元。主体、权限和范围由真实事实确认，观察到的署名、Git 身份或模型调用不自动证明权属。结构通过不等于权属或许可兼容性认证。

目前支持经过逐版本登记和测试的 `scope-license-v1` 工作流；收录一份协议不表示已支持自动生成。安装后的 Skill 携带固定目录和资产，无需网络、Git 或两个源码仓库。

## 快速开始

在工具仓库根目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r tooling/requirements-dev.txt
make test

# 显式取得协议仓库并核验 sources.lock 中的固定提交
python3 -B tooling/yunxia.py source fetch --id YunXia-Agreements
make check
```

协议仓库使用独立 `.git` 和远端，不是 submodule。父仓只提交来源锁，不提交 `repositories/` 的任何文件或 gitlink。该目录是协议工作区，不是可以自动删除的缓存。

查看协议工作信息与固定目录：

```bash
python3 -B tooling/yunxia.py agreement --repository repositories/YunXia-Agreements inspect --id yunxia-license
python3 -B tooling/yunxia.py check --repository repositories/YunXia-Agreements
python3 -B tooling/yunxia.py project catalog
```

首次获取的协议工作树位于远端默认分支，已有工作树在获取时保持原分支。维护命令操作指定协议仓库，构建则读取来源锁指定的提交，不混入协议工作树的未提交内容。来源更新是显式步骤，不自动跟随 main、latest 或推荐项；详见 [开发指南](docs/development.md)。

构建并更新本机 Skill：

```bash
make build
make skill-install
```

构建包位于 `dist/skills/yunxia/`。`skills/yunxia/` 是指令源码，安装使用构建产物。安装切换只暂存当前事务需要的旧目录，成功后清理；不再创建长期安装备份或全仓副本，失败时保留恢复现场。

安装后，可以向 Agent 提出：

> 用 yunxia 检查这个项目，为指定范围采用允夏许可证的确切草案版本，缺少的事实再问我。

选择协议、生成草稿和确认采用分别记录。协议维护按 [完整流程](docs/maintenance.md) 进行，不由项目采用 Skill 创建或发布协议。

## 目录结构

```text
.
├── AGENTS.md
├── sources.lock.json            # 固定协议来源
├── tooling/                     # 管理、适配和构建引擎
│   ├── src/yunxia/
│   ├── schemas/
│   └── tests/
├── skills/yunxia/               # 项目采用 Skill 指令
├── docs/                        # 通用流程、架构和开发说明
├── repositories/                # 父仓忽略的独立协议仓库
│   └── YunXia-Agreements/
└── dist/                        # 忽略的构建与检查产物
```

协议仓库包含 `agreements/`、`registry/`、来源迁移清单和系列原则，以 `toolchain.lock.json` 固定维护工具提交。仓库身份、来源锁、历史取材和分发边界见 [架构说明](docs/architecture.md)。两个仓库可分别克隆；构建前必须具备来源锁所需的完整对象，缺失时明确失败，不回退到当前正文。

## 文档导航

| 阅读目的 | 文档 |
| --- | --- |
| 让 Agent 参与维护或选择任务 | [AGENTS.md](AGENTS.md) · [任务导航](docs/README.md) |
| 理解两个仓库和固定来源 | [架构与固定来源](docs/architecture.md) |
| 立项、修改、审阅和发布协议 | [协议全周期维护](docs/maintenance.md) |
| 提交推送或继续已有任务 | [Git 阶段约定](docs/maintenance.md#git) · [继续维护](docs/maintenance.md#resume) |
| 开发、更新来源、构建和安装 | [开发指南](docs/development.md) |
| 阅读具体协议和系列原则 | [协议仓库](https://github.com/XiaTian-X/YunXia-Agreements) |
| 通过 Agent 适配具体项目 | [Skill 入口](skills/yunxia/SKILL.md) · [项目工作流](skills/yunxia/references/workflow.md) |
| 恢复项目未完成事务 | [事务恢复](skills/yunxia/references/recovery.md) |
| 查看工具变化 | [变更记录](CHANGELOG.md) |

## 仓库自身的许可与来源

协议收录、工具安装和技术检查不自动形成对本仓库内容的统一授权。仓库自身正式采用及完整权属事实仍待确认，具体范围和来源见 [项目说明](docs/project-rights.md)。私有证据、凭证、环境、缓存、生成物和采用项目状态不进入公开提交。
