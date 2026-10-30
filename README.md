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
| 维护工具与 Skill | 0.4.0 |
| 项目采用状态格式 | 仅支持格式 3 |
| 运行环境 | Python 3.11 或以上 |

`releases/` 中保留了三份标记为 `imported-draft` 的旧草案快照，用于草案版本对照。其中 `0.9.3-draft.2` 对应当前命名草案；这些标识用于区分固定文本，不代表正式发布或条款修改。

## 项目能力

### 协议全周期维护

- **设立与起草**：建立稳定协议 ID，说明设立目的、适用对象和设计取舍。
- **审阅与冻结**：记录审阅结论，将正文、说明、场景和模板等材料绑定到确切摘要，形成固定候选。
- **发布与更新**：核对发布文件，保留版本说明与发布记录，从指定基线开始后续修订。
- **推荐与维护状态**：分别管理推荐版本、暂停和停止维护，保留已有固定版本。

工作内容通过 Git 记录变化，固定版本按摘要校验。已发布正文保持固定，新的条款变化进入新版本；工具更新和推荐项变化不会自动改变项目已经采用的协议。

当前发布功能实现为**经文件核对的本地目录交付**，远程托管与对外发布需另行执行和验证。

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

核心引擎和构建后的 Skill 仅使用 Python 标准库。完整测试中的 JSON Schema 契约检查需要 `jsonschema`，开发依赖已列在上述文件中。

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

### 3. 构建和安装 Skill

```bash
# 构建独立分发包
make build

# 构建、备份并更新本机安装
make skill-install
```

构建产物位于 `dist/skills/yunxia/`，包含独立运行所需的代码与固定协议资产。安装器将其同步到本机 Skill 目录，并先备份已有安装。`skills/yunxia/` 是指令源码目录，安装应使用构建产物。

安装后，可以向 Agent 提出：

> 用 yunxia 检查这个项目，为指定范围采用允夏许可证的确切草案版本，缺少的事实再问我。

选择协议、生成草稿与确认采用是不同步骤。已有确认可以复用，未知事实保持待核；工具升级不会补造授权或自动更换协议。

## 目录结构

```text
.
├── agreements/             # 各协议的工作正文、设计、审阅和人工采用指南
│   └── yunxia-license/
├── releases/               # 固定草案、候选及正式发布记录，状态分别标明
├── tooling/                # 独立维护与适配引擎
│   ├── src/yunxia/         # 目录、生命周期、项目工作流及适配器
│   ├── schemas/            # 数据格式
│   └── tests/              # 当前格式、边界和故障恢复测试
├── skills/yunxia/          # Agent 指令与参考文档
├── docs/                   # 系列规则、维护流程及仓库来源说明
└── dist/                   # 构建包和检查报告，不提交 Git
```

协议专属资料随协议维护；可执行能力集中在独立引擎中，Skill 负责对话衔接。重复历史归档已移出日常维护目录，必要的设计依据、人工场景和来源事实继续保留。

## 常用维护命令

| 命令 | 用途 |
| --- | --- |
| `make test` | 运行全部回归测试 |
| `make check` | 检查目录、元数据、固定摘要、本地链接及测试 |
| `make agreements-check` | 检查仓库结构和协议数据，不运行全量测试 |
| `make build` | 构建独立 Skill 分发包 |
| `make skill-install` | 构建、备份并更新本机 Skill |

协议维护命令包括 `create`、`inspect`、`review`、`freeze`、`publish`、`revise`、`recommend`、`maintain` 和 `retire`。具体输入与阶段要求见 [协议全周期维护指南](docs/maintenance.md)。

检查只验证已实现的结构、记录和文件一致性，不自动接受变化后的摘要。环境、缓存、构建产物、项目私有状态和常见凭证由 `.gitignore` 排除；实际交付仍需检查文件范围。

## 文档导航

| 阅读目的 | 文档 |
| --- | --- |
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
