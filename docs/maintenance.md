# 协议全周期维护

本指南是通用维护流程。协议仓库保存当前工作材料、registry、审阅与发布事实；工具仓库提供操作能力。先读目标协议仓库的 `AGENTS.md` 和系列原则，并按 [架构说明](architecture.md) 确认两个独立 Git 根。

先按 [固定工具链准备](development.md#toolchain) 核对协议仓库指定的工具提交和工作区。下列命令从已选定的工具仓库根执行；若工具位于协议仓库的 `.yunxia-tool/`，把协议路径替换为实际协议根的绝对路径，不沿用默认嵌套路径。从其他工作目录执行时使用工具入口的绝对路径。`--repository` 必须指向协议仓库，不能指向父工具仓库或尚未初始化的子目录。

```bash
yunxia_protocol="repositories/YunXia-Agreements"
git -C "$yunxia_protocol" rev-parse --show-toplevel
python3 -B tooling/yunxia.py check --repository "$yunxia_protocol"
```

目标仓库的 `agreements/` 保存工作资料，`registry/<协议ID>/<版本>.json` 登记固定版本及发布事实，历史正文从完整 Git 提交和真实路径提取。标签指向源码提交，登记和发布回执可以在后续提交保存。工具不自动替用户提交或推送，Agent 按 [Git 阶段约定](#git) 完成已授权操作。

从 [任务导航](README.md) 选择章节。示例中的协议 ID、版本、路径和发布决定需替换为实际值；已有协议先运行 `agreement --repository "$yunxia_protocol" inspect --id <协议ID>` 查看工作信息和固定版本。命令本身及输入字段以已选定工具的帮助和检查结果为准。

<a id="create"></a>

## 设立与起草

用户可以只提出目标，不必先准备完整正文。Agent 先读取已有协议及相关设计，整理适用对象、拟解决的问题、希望允许和限制的行为，以及与现有协议的关系；判断应新增协议、修改现有工作稿，还是从固定版本发起修订。

把已有事实、用户决定、Agent 建议和待核事项分开。只集中补问会改变条款或采用范围的关键缺口，每轮通常不超过三组；提供已找到的依据和可选取舍，未知信息保留待核，不猜测主体、权限或兼容结论。用户已明确的决定直接复用，等待答案时继续独立可完成的研究和资料整理。

目标与关键取舍足以起草后，由 Agent 编写初始正文，使用真实临时输入文件调用 `create`。稳定 ID、显示名称、设立目的和目标版本应明确；仅换名不能冒充新的协议效果。初稿建立后，把设计理由写入 `design.md`，问题和未决事项写入 `review.md`，不再另建一套任务状态库。

以下文件路径是输入示例，替换为实际准备的初稿：

```bash
python3 -B tooling/yunxia.py agreement --repository "$yunxia_protocol" create --id example-license --name 示例协议 --purpose 实际设立目的 --version 1.0.0 --text /private/initial-text.txt
```

命令只建立草案。Agent 根据已确认的目标补齐 README 中的定位、设计依据、审阅问题和人工采用路径，并按实际场景添加 `cases.json` 与必要模板。模板应服务真实采用方式，不引入正文中没有的许可条件。检查正常使用、边界情形和容易误解的情形，给后续审阅提供可讨论的具体案例。`create` 的占位说明不表示工作已完成；日常编辑通过 Git 留痕，并行修订使用分支。

新增另一个协议通常只需在同一协议仓库调用 `create`。需要新的独立协议仓库时，先在实际目标目录执行 `git init`，再用 `repository init --repository <目标目录> --id <仓库ID>` 初始化仓库身份，随后建立该仓入口和原则；不要把协议 ID 与仓库 ID 混用。

完成条件：工作目录已建立，定位与材料已整理，未决项有记录，`inspect` 和仓库检查通过。后续批准与候选冻结进入下一流程。

<a id="edit"></a>

## 修改工作材料

修改前先 `inspect`，核对工作稿、固定基线、审阅是否仍有效以及未提交改动。继续当前草案可直接修改工作材料；从固定版本开始下一版时，先按 [版本修订](#revision) 执行 `revise`，不能先改正文再用该命令覆盖变化。

Agent 应说明修改原因、涉及条款、行为变化和受影响案例，再同步设计、采用指南及必要模板。仅说明或显示名称变化也可能改变审阅材料摘要，必须重新核对原审阅；正文中名称改变属于正文修改。协议 ID、旧版本和固定记录保持不变。更名的理由及原名称在当前说明中记录，不追溯重写已固定草案、候选或正式版，也不自动修改旧项目采用。

修改显示名称时，使用显式更名操作并记录实际原因：

```bash
python3 -B tooling/yunxia.py agreement --repository "$yunxia_protocol" rename --id example-license --name 新显示名称 --reason 实际更名理由
```

`rename` 只修改当前元数据名称并追加名称历史，清除旧批准并回到 draft；相同名称不产生新变更。它不修改正文、协议 ID、历史 registry 或标签。Agent 随后按实际任务更新当前介绍和导航，正文中的名称如需变化仍须按草案修订或新版本处理。暂停或已停止维护的协议先按真实决定恢复维护状态，再更名。

修改后给出差异和影响，核对正文与说明一致、旧引用仍可解析、未决项没有被删除或误写成确认。实际结论进入审阅流程；只要求讨论或审查的任务不扩展成修改、提交或发布。

<a id="review"></a>

## 审阅与冻结

输入是当前完整材料及实际审阅决定；冻结还需要未占用的候选标识和版本说明。Agent 审阅定义与主体是否一致、条款是否冲突、许可范围及例外是否清楚、条件和后果是否衔接，以及场景、模板、采用指南是否符合正文。对版本更新还要说明相对基线的变化及旧项目可能受影响的部分。

来源引用和比较结论应有可追溯依据；无法确认的事实保持待核。需要独立复核时可委派其他 Agent，但如实记录其参与范围，不能把模型意见写成真实用户、组织或其他审阅者的决定。

审阅分析写入该协议的 `review.md`；已确认的设计取舍保存在 `design.md`。实际批准或要求修改的结论通过工具写入维护元数据。复用当前任务中真实、足够明确的委托和决定；只有批准或发布范围确实缺失时才补问，先把待决定材料整理到可审阅状态，不重复要求已给出的授权。

```bash
python3 -B tooling/yunxia.py agreement --repository "$yunxia_protocol" inspect --id example-license
python3 -B tooling/yunxia.py agreement --repository "$yunxia_protocol" review --id example-license --input /private/review.json
```

review.json必须有source_sha256（inspect返回）、reviewer、date、decision（approved或changes-required）、basis和open_issues。approved不能带未解决事项。摘要绑定正文、定位、设计、审阅分析、采用指南、存在的场景和模板，以及修订基线；材料改变后旧批准失效。批准记录自身和推荐等管理状态不参与递归摘要。工具记录实际提供的审阅决定，reviewer字段不自动证明身份或权限，测试和迁移不能代替批准。

需要引用共享规则时，在该协议agreement.json的可选review_dependencies列表中声明仓库相对文件路径，例如docs/principles.md。依赖限于docs/或agreements/下的普通资料文件，不能指向私有路径、协议元数据或发布登记。审阅清单按格式1保存这些依赖的原始字节摘要；依赖内容或声明范围改变后须重新审阅。冻结时依赖也必须已提交，之后始终从该固定提交读取。审阅材料的增加、删除和重命名都须进入该源码提交；文件集合不一致时在创建候选标签与登记前拒绝。未声明依赖时不生成空依赖字段。

审阅后先提交实际材料、审阅记录以及声明的共享依赖，再冻结候选。按当前任务已授权的范围核对差异后提交；示例未替实际共享依赖填写路径：

```bash
git -C "$yunxia_protocol" add agreements/example-license
# 若声明了共享依赖，同时暂存其中已修改的实际文件
git -C "$yunxia_protocol" commit -m "Review example-license 1.0.0"
python3 -B tooling/yunxia.py agreement --repository "$yunxia_protocol" freeze --id example-license --version 1.0.0-rc.1 --notes /private/notes.md
```

freeze核对已提交材料与当前审阅，保存源码提交、文件映射、原始字节摘要、审阅记录和版本说明，并创建本地注释标签example-license/v1.0.0-rc.1。候选固定后不能改绑源码；标签存在但指向不同提交会失败。工具不执行历史提交中的代码，不以当前工作正文替代缺失历史对象。候选号必须不同于target_version，已有版本号不能重复用于新修订。

若中断发生在候选标签已创建、registry 尚未写入之间，保持原协议、候选号、材料和说明输入，重试同一冻结操作。工具沿用现有标签的原源码提交重新核对；HEAD 因无关提交前进不会要求移动标签，审阅材料改变则拒绝恢复。登记已经存在时先核验既有候选，不覆盖或重新占用版本号。

Git保存历史字节；registry保存版本身份、审阅和发布事实。候选中的小型说明与审阅记录可由登记生成，协议正文不在登记中复制。后续构建生成包内文件清单与摘要，包清单不包含自己的摘要或未来发布回执。

registry中的审阅依据、版本说明和发布决定会进入公开登记或发布包，只填写可公开的摘要与必要定位信息；私人合同、凭证和完整内部证据另行保存。校验覆盖完整Git历史，包括合并分支及合并提交中的登记，并忽略本地grafts/replace对历史视图的替换；拒绝版本改绑、发布回执或撤回事实的删除。

完成条件：仅审阅任务交付实际结论和未决项；需要冻结时，批准仍覆盖当前材料，候选登记为 `status=candidate`，源码和标签可解析，仓库检查通过。工作元数据的 `stage` 与固定版本的 `status` 分开，不用工作阶段代替候选或发布结果。

<a id="local-publication"></a>

## 发布决定与本地交付

先用inspect取得候选fixed_versions中的package_sha256。publication.json必须有date、confirmed_by、basis和candidate_sha256；最后一个字段使用整个候选包摘要，不是正文sha256或审阅摘要。

```bash
python3 -B tooling/yunxia.py agreement --repository "$yunxia_protocol" inspect --id example-license
python3 -B tooling/yunxia.py agreement --repository "$yunxia_protocol" publish --id example-license --candidate 1.0.0-rc.1 --version 1.0.0 --destination /outside/releases/example-license-1.0.0 --input /private/publication.json
```

正式版本必须等于候选来源中已审阅的target_version。先登记prepared发布意图，再向仓库外目录交付、逐项核对实际文件并登记完成回执。本地渠道表示经过核对的本地交付，不表示远程URL可用。目标已有不同内容时拒绝覆盖；中断后使用相同输入重试，复用一致的交付并补齐回执。

完成后运行 `agreement --repository "$yunxia_protocol" verify-publication --id example-license --version 1.0.0` 核对实际目录；登记应为 `status=released` 且 `publication.state=published`。单独使用 `--prepare` 只占用版本身份并保存发布意图，不满足交付完成条件。

<a id="github-publication"></a>

## GitHub发布与恢复

GitHub 渠道使用 github.com，需要已认证的 gh、明确的协议仓库、候选和发布决定。OWNER/REPO 替换为协议仓库的实际目标，不能使用父工具仓库的 origin；示例中的协议仓库 origin 必须指向该目标。当前允夏协议尚无正式发布，以下命令只用于实际获得发布授权的任务。

```bash
python3 -B tooling/yunxia.py agreement --repository "$yunxia_protocol" publish --id example-license --candidate 1.0.0-rc.1 --version 1.0.0 --github OWNER/REPO --input /private/publication.json --prepare
# 核对并提交prepared、对应候选与修订基线登记及协议元数据，推送到目标仓库
# 推送本次依赖的候选标签；其他基线标签同样逐项推送
git -C "$yunxia_protocol" push origin example-license/v1.0.0-rc.1
python3 -B tooling/yunxia.py agreement --repository "$yunxia_protocol" publish --id example-license --candidate 1.0.0-rc.1 --version 1.0.0 --github OWNER/REPO --input /private/publication.json
# 核对完成回执后，提交并推送更新的registry
python3 -B tooling/yunxia.py agreement --repository "$yunxia_protocol" verify-publication --id example-license --version 1.0.0
```

准备阶段占用正式版本身份并固定来源、标签、发布决定和附件摘要。登记及其候选、修订基线等依赖必须在同一已提交快照中完整可读，并已推送；依赖标签也必须可从远端解析到固定提交。发布工具在任何远端写入前核对这些条件，随后核对正式版本标签、创建或恢复draft Release、核对已有附件并补齐缺失项，最后公开并下载验证，再写完成回执。工具不替维护者提交或推送任意工作树文件。

存在迁移清单时，固定的历史来源分支也必须在远端存在并指向登记提交；仅旧提交仍可读取，不能代替分支保留核验。

跨Git和GitHub的操作不能作为单次原子事务。上传超时后，使用相同输入重试，工具会先查询实际远端结果；同名同摘要附件可以复用，同名不同内容必须报冲突，不覆盖。启用不可变发布时必须在公开前上传全部附件。Release已公开但本地回执尚未写入时，使用相同发布输入核验并补写；回执已经写入但尚未提交或推送时，先核对并提交、推送更新的登记，再运行 `verify-publication`。不能删除已公开结果来伪装回滚。

完成条件：公开附件实际下载核验通过，登记为 `status=released`、`publication.state=published`，完成回执已提交并推送，`verify-publication` 返回发布与登记同步核验通过。仅 `prepared`、仅有公开附件或仅有本地回执，都应报告具体未完成项。

`publication.state=published` 记录历史交付事实，当前远端不可用另由核验结果报告，不能把已发布版本退回未发布。Release标题、说明、latest和预发布标记不是固定内容的唯一依据。主体版本、原始正文摘要、标签目标和登记的附件集合均须匹配。

<a id="revision"></a>
<a id="更新推荐与维护状态"></a>

## 更新版本

修订输入是可解析的固定基线和未占用的新目标版本。先执行 `revise` 再编辑下一版正文。

```bash
python3 -B tooling/yunxia.py agreement --repository "$yunxia_protocol" revise --id example-license --base 1.0.0 --version 2.0.0
```

revise要求工作正文仍与基线一致，防止覆盖已编辑内容；它绑定下一版目标及基线，清除旧批准，正文随后由维护者修改。已发布正文任何改变都需要新版本。历史引用链必须可从固定Git对象解析，缺失或摘要不一致会使检查失败。

修订完成条件：新目标与基线已记录，工作草案及配套资料按要求更新，旧固定版本和已有引用保持可解析；随后按 [审阅与冻结](#review) 形成新候选。修改工作草案不会自动生成新发布。

<a id="maintenance-state"></a>

## 推荐与维护状态

输入是实际目标版本或维护状态及其理由；按任务选择所需命令：

```bash
python3 -B tooling/yunxia.py agreement --repository "$yunxia_protocol" recommend --id example-license --version 1.0.0 --reason 实际推荐理由
python3 -B tooling/yunxia.py agreement --repository "$yunxia_protocol" recommend --id example-license --reason 停止推荐理由
python3 -B tooling/yunxia.py agreement --repository "$yunxia_protocol" retire --id example-license --reason 停止维护理由
python3 -B tooling/yunxia.py agreement --repository "$yunxia_protocol" maintain --id example-license --state active --reason 恢复维护理由
```

recommend只能指向可推荐的正式版，不填version表示停止推荐。retire清除推荐并停止维护，历史记录仍保留；maintain支持active、paused和retired。维护状态、推荐和适配支持分别管理，不因工具升级或新版本发布改变旧项目绑定。

主动撤回分发使用实际决定文件（date、reason、confirmed_by）：

```bash
python3 -B tooling/yunxia.py agreement --repository "$yunxia_protocol" withdraw --id example-license --version 1.0.0 --input /private/withdrawal.json
```

撤回保留原版本和发布证据，停止推荐；不能删除正文来表达撤回，也不自动改变既有项目的采用记录或推定其法律效果。

状态调整完成后，用带协议仓库路径的 `inspect` 和协议检查核对当前状态、推荐及保留的引用。`project catalog` 反映来源锁或安装包快照，只有显式更新来源后才包含新的维护状态；不能拿旧快照判定本次修改未生效。不要把停止维护与撤回分发混为同一个决定。

<a id="git"></a>

## Git 阶段约定

工具负责受校验的文件与发布操作，Git 提交和推送由维护者或已获授权的 Agent 执行。先确认任务范围、实际仓库根、分支、远端和工作树差异，识别其他未提交工作，不把它们一起暂存，也不擅自丢弃或移动。协议改动用 `git -C "$yunxia_protocol"`，工具、来源锁和适配改动提交到工具仓库；父仓索引不得包含 `repositories/` 文件或 gitlink。

在既有授权内按阶段持续执行，不逐条命令请求确认。没有覆盖提交、推送或正式发布的授权时，只补问缺失的具体范围；检查或修复授权不能自动扩大成正式发布。阶段提交应只包含实际需要的文件，检查私有证据、凭证、生成物和无关改动，并运行适合本次变化的检查。

| 节点 | 必须保存的事实 | 下一步 |
| --- | --- | --- |
| 审阅完成 | 正文、完整审阅材料、实际决定及声明的依赖 | 提交后冻结候选 |
| 候选冻结 | 候选登记和固定标签 | 核对候选与已有版本说明 |
| 发布准备 | prepared 登记、候选、修订基线和协议元数据 | 提交推送并核对依赖标签后公开发布 |
| 发布完成 | 实际下载核验后的完成回执 | 提交推送，再运行发布核验 |
| Skill 接入 | 明确选择的协议目录提交、适配登记及测试 | 在工具仓库提交后构建和验收 |

提交成功不等于推送成功，推送成功不等于协议已发布。失败时报告本地提交、实际远端状态及下一步，按原输入核验重试；不得强推改写历史或删除已公开结果伪装回滚。不创建操作前全仓备份、Git bundle 或长期安装副本。

<a id="resume"></a>

## 继续维护

新会话或接手其他 Agent 的任务时，不仅依赖聊天中的进度描述。先读取目标仓库入口，核对工具链锁及实际执行工具，再读取现有设计和审阅未决项，运行带目标仓库参数的 `inspect`，核对 Git 工作树、分支、registry、标签以及任务确实需要的远端状态。区分正在编辑的草案、已固定候选、prepared 意图、已公开附件、已写回执和已同步回执；不要用旧 Skill 的目录快照代替协议仓库当前状态。

从已确认事实重建目标、当前阶段、修改依据、剩余缺口和有效授权。记录中的来源和范围不清时只补问影响下一步的部分，不将旧任务的授权扩大到新版本或新渠道。复用 `design.md`、`review.md`、协议元数据与 registry 中的事实，不另建重复的进度数据库，不为续接编造确认日期或审阅身份。

优先处理未完成事务或发布恢复，再推进新修改。交接时说明实际修改文件、提交和推送状态、检查结果、未决项及下一步；私人材料只保留必要定位，不自动写入公开审阅或发布记录。

<a id="recovery"></a>

## 异常与恢复

先保留现场，确认失败发生在维护仓库、发布渠道还是目标项目。恢复入口按对象选择：

| 情况 | 处理方式 | 完成判据 |
| --- | --- | --- |
| 维护命令中断后遗留 PID 锁 | 从工具仓库执行 `python3 -B tooling/yunxia.py recover-maintenance --repository "$yunxia_protocol"`；只回收已结束进程的锁，再重试原操作 | 锁恢复结果明确，原操作及相应检查通过 |
| 冻结中断，仅留下原候选标签 | 按 [审阅与冻结](#review) 使用原输入重试，保留标签的原提交 | 候选登记形成且来源核验通过 |
| 本地交付或 GitHub 附件操作中断，完成回执尚未写入 | 按原协议、候选、版本、渠道和决定重试发布；具体条件见上方对应发布流程 | 原交付被核验，完成回执形成；GitHub 还需提交、推送和核验登记 |
| GitHub 回执已写入但未提交或推送 | 核对并提交、推送登记，再执行 `verify-publication` | 远端附件和完成登记同时核验通过 |
| 构建或安装报告需要恢复 | 按错误返回的事务暂存和目标路径核对并恢复；成功后清理临时旧目录，机制见 [开发指南](development.md) | 可用包与原固定摘要一致，再完成构建或安装检查 |
| 目标项目材料写入中断 | 按 [项目事务恢复](../skills/yunxia/references/recovery.md) 对发生中断的采用单元操作 | 原字节与已记录权限恢复，重新扫描并报告检查结果 |

`recover-maintenance` 只处理维护锁。活跃锁保持不动，保留的 guard 文件不是待删除的锁残留；说明见 [开发指南](development.md)。Git对象缺失、摘要冲突或人工修改冲突应根据真实来源处理，不通过删状态、改摘要或覆盖标签绕过。恢复完成前报告已完成和仍待核的部分。

<a id="adaptation"></a>

## 适配与历史保留

发布与Skill适配独立。人工采用指南按具体协议编写；adapter登记和真实输入/输出测试决定能否自动生成。登记绑定协议ID、版本及正文摘要，不能按系列名称或版本前缀推断兼容。新增版本支持后重新构建和安装Skill。

三份历史版本保留imported-draft身份、原有名称、说明、正文摘要和空发布事实。迁移绑定现有Git历史中实际存在的releases路径，原source.path仅作为历史来源注记保留；迁移不补造审阅或正式发布。

协议仓库须保留完整 Git 历史及迁移清单固定的历史来源分支。构建与登记校验需要这些真实对象；浅克隆先补齐历史，缺少对象时明确失败，不自动改 pin、用 HEAD 替代或重新归一化正文。必要历史对象属于引用依赖，不额外生成备份。Skill 的来源更新与构建见 [开发指南](development.md)，离线包不能获知构建后的维护变化。
