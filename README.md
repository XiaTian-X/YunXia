<p align="center">
  <img src="https://raw.githubusercontent.com/XiaTian-X/xia-logo/main/yunxia/latest/yunxia-primary-latest.svg" alt="允夏 Yǔn Xià" width="360">
</p>

<h1 align="center">允夏 YǔnXià</h1>

<p align="center">
  <strong>维护允夏系列许可协议，让协议的形成、发布和项目采用都有清晰依据。</strong>
</p>

<p align="center">
  <a href="#当前状态">当前状态</a> ·
  <a href="#项目能力">项目能力</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#目录结构">目录结构</a> ·
  <a href="#文档导航">文档导航</a>
</p>

---

允夏（拼音：yǔn xià）管理每个具体许可协议从设立、起草、审阅到发布、更新和停止维护的全过程，并通过 **yunxia Skill** 帮助具体项目采用指定协议及确切版本。

协议维护与项目采用分别进行：维护者负责协议文本及版本，项目使用者确认实际采用主体和内容范围，工具负责整理事实、生成材料和检查一致性。

## 当前状态

| 项目 | 状态 |
| --- | --- |
| 已收录协议 | [允夏许可证](agreements/yunxia-license/README.md) |
| 当前工作正文 | [0.9.3 审阅草案](agreements/yunxia-license/text.txt) |
| 正式发布版本 | 暂无 |
| 维护工具与 Skill | 0.5.2 |
| 项目采用状态格式 | 仅支持格式 3 |
| 运行环境 | Python 3.11 或以上；写入与恢复需要 macOS/Linux 等支持 POSIX 文件锁的系统 |

`registry/` 登记三份标记为 `imported-draft` 的历史草案，按完整 Git 提交与文件路径读取固定正文。其中 `0.9.3-draft.2` 对应当前命名草案；原版本号、正文摘要和草案身份保持不变，迁移没有创建正式发布。工作树不再保存重复的历版正文目录。

## 项目能力

### 协议全周期维护

- **设立与起草**：建立稳定协议 ID，说明设立目的、适用对象和设计取舍。
- **审阅与冻结**：记录审阅结论，将正文、说明、场景和模板等材料绑定到确切摘要，形成固定候选。
- **发布与更新**：核对发布文件，保留版本说明与发布记录，从指定基线开始后续修订。
- **推荐与维护状态**：分别管理推荐版本、暂停和停止维护，保留已有固定版本。

工作内容与历史正文由 Git 保存；registry 记录版本身份、取材提交、正文摘要、审阅依据和发布事实。tag 是可核对的定位入口，完整提交与摘要固定实际内容。已发布正文保持固定，新的条款变化进入新版本；工具更新和推荐项变化不会自动改变项目已经采用的协议。

发布流程先登记固定内容与交付意图，再交付并核对，最后保存完成回执；中断后使用相同输入核验重试。工具支持本地目录交付及 GitHub Release 渠道，测试使用隔离仓库和模拟远端，不创建本仓库的正式发布。

### 具体项目采用

yunxia Skill 调用独立适配引擎，完成以下工作：

1. 明确指定协议及确切版本，检查工具支持程度。
2. 扫描项目内容、已有许可、第三方声明和交付范围。
3. 复用已有确认，集中补问影响采用的缺失事实。
4. 保持协议正文原字节，生成范围声明及必要配套材料。
5. 再次运行时核对变化，检查实际目录或 ZIP 交付物。

同一项目可以按代码、文档等内容范围建立不同采用单元。各单元分别保存协议绑定和确认记录，共享第三方材料可以保留原声明；历史回答不会占用已经移出当前范围的文件。

目前已实现 `scope-license-v1` 适配工作流。其他工作流需要补充实现与测试；收录一份协议不等于已经支持自动采用。

## 快速开始

在仓库根目录执行以下命令。

### 1. 准备开发环境

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r tooling/requirements-dev.txt
make check
```

Python 引擎使用标准库；维护与构建需要 Git 对象历史，GitHub 发布渠道另需已认证的 `gh`。构建后的 Skill 无需 Git、网络或维护仓库。完整测试中的 JSON Schema 契约检查需要 `jsonschema`，开发依赖已列在上述文件中。

### 2. 查看协议和检查项目

```bash
# 查看固定版本及适配能力
python3 -B tooling/yunxia.py project catalog

# 查看协议工作信息与审阅状态
python3 -B tooling/yunxia.py agreement inspect --id yunxia-license

# 只读扫描目标项目
python3 -B tooling/yunxia.py project --root /path/to/project scan --read-only
```

`catalog` 列出固定版本、状态及适配能力；`inspect` 展示协议工作信息和审阅状态。将 `/path/to/project` 替换为目标项目的绝对路径，只读扫描不会写入采用状态。

完整采用流程及回答格式见 [Skill 工作流](skills/yunxia/references/workflow.md)。

在本仓库通过 Agent 新增、审阅或维护协议，从 [Agent 工作入口](AGENTS.md) 和 [任务导航](docs/README.md) 开始；具体项目采用继续使用 `yunxia` Skill。

### 3. 构建和安装 Skill

```bash
# 构建独立分发包
make build

# 构建、备份并更新本机安装
make skill-install
```

构建产物位于 `dist/skills/yunxia/`，包含独立运行所需的代码、版本目录与固定协议资产。目录反映构建时的维护状态，离线运行不会自动获知后来发生的推荐或撤回变化。安装器将其同步到本机 Skill 目录，并先备份已有安装。`skills/yunxia/` 是指令源码目录，安装应使用构建产物。

安装后，可以向 Agent 提出：

> 用 yunxia 检查这个项目，为指定范围采用允夏许可证的确切草案版本，缺少的事实再问我。

选择协议、生成草稿与确认采用是不同步骤。已有确认可以复用，未知事实保持待核；工具升级不会补造授权或自动更换协议。

## 目录结构

```text
.
├── AGENTS.md               # 仓库级 Agent 入口与任务导航
├── agreements/             # 各协议的工作正文、设计、审阅和人工采用指南
│   └── yunxia-license/
├── registry/               # 版本与发布登记，历史正文从固定 Git 提交提取
├── tooling/                # 独立维护与适配引擎
│   ├── src/yunxia/         # 目录、生命周期、项目工作流及适配器
│   ├── schemas/            # 数据格式
│   └── tests/              # 当前格式、边界和故障恢复测试
├── skills/yunxia/          # Agent 指令与参考文档
├── docs/                   # 系列规则、维护流程及仓库来源说明
└── dist/                   # 构建包和检查报告，不提交 Git
```

协议专属资料随协议维护；可执行能力集中在独立引擎中，Skill 负责对话衔接。registry 只登记历史正文的确切 Git 来源，不执行历史提交中的工具。浅克隆或缺失对象会明确报错，不能回退到工作正文；维护者须保留可取得的完整历史。必要的设计依据、人工场景和来源事实继续保留。

## 常用维护命令

| 命令 | 用途 |
| --- | --- |
| `make test` | 运行全部回归测试 |
| `make check` | 检查目录、元数据、固定摘要、本地链接及测试 |
| `make agreements-check` | 检查仓库结构和协议数据，不运行全量测试 |
| `make build` | 构建独立 Skill 分发包 |
| `make skill-install` | 构建、备份并更新本机 Skill |

按新增、审阅、发布、更新或恢复等目标选择 [维护任务](docs/README.md)，具体命令、输入与完成条件见对应流程。

检查只验证已实现的结构、记录和文件一致性，不自动接受变化后的摘要。环境、缓存、构建产物、项目私有状态和常见凭证由 `.gitignore` 排除；实际交付仍需检查文件范围。

## 文档导航

| 阅读目的 | 文档 |
| --- | --- |
| 通过 Agent 维护协议或选择任务流程 | [Agent 工作入口](AGENTS.md) · [任务导航](docs/README.md) |
| 了解系列定位与维护原则 | [允夏系列原则](docs/principles.md) |
| 设立、审阅、发布和更新协议 | [协议全周期维护](docs/maintenance.md) |
| 开发工具、运行测试和安装 | [开发、构建与安装](docs/development.md) |
| 人工采用当前协议 | [允夏许可证采用指南](agreements/yunxia-license/adoption.md) |
| 通过 Agent 适配项目 | [Skill 入口](skills/yunxia/SKILL.md) · [工作流](skills/yunxia/references/workflow.md) |
| 了解状态记录和恢复中断操作 | [状态与事务恢复](skills/yunxia/references/recovery.md) |
| 查看维护变化 | [变更记录](CHANGELOG.md) |

## 仓库自身的许可与来源

本仓库维护许可协议，也包含工具、模板和研究材料。协议的收录、工具安装及技术检查不会自动形成对这些内容的统一授权。

仓库自身的正式采用及完整权属事实仍待确认，具体范围和来源见 [项目说明](docs/project-rights.md)。项目采用时，应依据真实主体、内容范围和第三方许可作出安排；结构检查通过不代表已经完成权属或许可兼容性核验。
