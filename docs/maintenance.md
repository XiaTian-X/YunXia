# 协议全周期维护

在仓库根目录执行以下命令；维护其他仓库可在agreement后传入--repository。agreements保存当前工作资料，registry/<协议ID>/<版本>.json记录固定版本及发布事实，历史正文按完整Git提交和真实文件路径提取。tag统一指向该源码提交；登记文件与发布回执可以在后续提交中保存。

## 设立与起草

```bash
python3 -B tooling/yunxia.py agreement create --id example-license --name 示例协议 --purpose 实际设立目的 --version 1.0.0 --text /private/initial-text.txt
```

命令只建立草案。维护者补齐README中的定位、design中的设计依据、review中的问题和adoption中的采用路径；需要时添加cases.json与templates。create生成的待填写说明不表示审阅完成。日常编辑通过Git留痕，并行修订用分支。

## 审阅与冻结

```bash
python3 -B tooling/yunxia.py agreement inspect --id example-license
python3 -B tooling/yunxia.py agreement review --id example-license --input /private/review.json
```

review.json必须有source_sha256（inspect返回）、reviewer、date、decision（approved或changes-required）、basis和open_issues。approved不能带未解决事项。摘要绑定正文、定位、设计、审阅分析、采用指南、存在的场景和模板，以及修订基线；材料改变后旧批准失效。批准记录自身和推荐等管理状态不参与递归摘要。工具记录实际提供的审阅决定，reviewer字段不自动证明身份或权限，测试和迁移不能代替批准。

需要引用共享规则时，在该协议agreement.json的可选review_dependencies列表中声明仓库相对文件路径，例如docs/principles.md。依赖限于docs/或agreements/下的普通资料文件，不能指向私有路径、协议元数据或发布登记。审阅清单按格式1保存这些依赖的原始字节摘要；依赖内容或声明范围改变后须重新审阅。冻结时依赖也必须已提交，之后始终从该固定提交读取。未声明依赖时不生成空依赖字段。

审阅后先提交实际材料和审阅记录，再冻结候选。下面的提交必须由维护者确认其文件范围：

```bash
git add agreements/example-license
git commit -m "Review example-license 1.0.0"
python3 -B tooling/yunxia.py agreement freeze --id example-license --version 1.0.0-rc.1 --notes /private/notes.md
```

freeze核对已提交材料与当前审阅，保存源码提交、文件映射、原始字节摘要、审阅记录和版本说明，并创建本地注释标签example-license/v1.0.0-rc.1。候选固定后不能改绑源码；标签存在但指向不同提交会失败。工具不执行历史提交中的代码，不以当前工作正文替代缺失历史对象。候选号必须不同于target_version，已有版本号不能重复用于新修订。

Git保存历史字节；registry保存版本身份、审阅和发布事实。候选中的小型说明与审阅记录可由登记生成，协议正文不在登记中复制。后续构建生成包内文件清单与摘要，包清单不包含自己的摘要或未来发布回执。

registry中的审阅依据、版本说明和发布决定会进入公开登记或发布包，只填写可公开的摘要与必要定位信息；私人合同、凭证和完整内部证据另行保存。校验覆盖完整Git历史，包括合并分支及合并提交中的登记，并忽略本地grafts/replace对历史视图的替换；拒绝版本改绑、发布回执或撤回事实的删除。

## 发布决定与本地交付

先用inspect取得候选fixed_versions中的package_sha256。publication.json必须有date、confirmed_by、basis和candidate_sha256；最后一个字段使用整个候选包摘要，不是正文sha256或审阅摘要。

```bash
python3 -B tooling/yunxia.py agreement inspect --id example-license
python3 -B tooling/yunxia.py agreement publish --id example-license --candidate 1.0.0-rc.1 --version 1.0.0 --destination /outside/releases/example-license-1.0.0 --input /private/publication.json
```

正式版本必须等于候选来源中已审阅的target_version。先登记prepared发布意图，再向仓库外目录交付、逐项核对实际文件并登记完成回执。本地渠道表示经过核对的本地交付，不表示远程URL可用。目标已有不同内容时拒绝覆盖；中断后使用相同输入重试，复用一致的交付并补齐回执。

## GitHub发布与恢复

GitHub渠道使用github.com，需要已认证的gh，OWNER/REPO替换为明确的目标仓库。本仓库没有正式发布；以下命令是维护者未来明确决定发布时使用的流程。

```bash
python3 -B tooling/yunxia.py agreement publish --id example-license --candidate 1.0.0-rc.1 --version 1.0.0 --github OWNER/REPO --input /private/publication.json --prepare
# 核对并提交prepared、对应候选与修订基线登记及协议元数据，推送到目标仓库
# 推送本次依赖的候选标签；其他基线标签同样逐项推送
git push origin example-license/v1.0.0-rc.1
python3 -B tooling/yunxia.py agreement publish --id example-license --candidate 1.0.0-rc.1 --version 1.0.0 --github OWNER/REPO --input /private/publication.json
# 核对完成回执后，提交并推送更新的registry
python3 -B tooling/yunxia.py agreement verify-publication --id example-license --version 1.0.0
```

准备阶段占用正式版本身份并固定来源、标签、发布决定和附件摘要。登记及其候选、修订基线等依赖必须在同一已提交快照中完整可读，并已推送；依赖标签也必须可从远端解析到固定提交。发布工具在任何远端写入前核对这些条件，随后核对正式版本标签、创建或恢复draft Release、核对已有附件并补齐缺失项，最后公开并下载验证，再写完成回执。工具不替维护者提交或推送任意工作树文件。

跨Git和GitHub的操作不能作为单次原子事务。上传超时后先查询实际远端结果；同名同摘要附件可以复用，同名不同内容必须报冲突，不覆盖。启用不可变发布时必须在公开前上传全部附件。Release已公开但本地回执未写入，或回执未推送时，重试相同发布命令后完成登记；不能删除已公开结果来伪装回滚。

published记录历史交付事实，当前远端不可用另由核验结果报告，不能把已发布版本退回未发布。Release标题、说明、latest和预发布标记不是固定内容的唯一依据。主体版本、原始正文摘要、标签目标和登记的附件集合均须匹配。

## 更新、推荐与维护状态

```bash
python3 -B tooling/yunxia.py agreement revise --id example-license --base 1.0.0 --version 2.0.0
python3 -B tooling/yunxia.py agreement recommend --id example-license --version 1.0.0 --reason 实际推荐理由
python3 -B tooling/yunxia.py agreement recommend --id example-license --reason 停止推荐理由
python3 -B tooling/yunxia.py agreement retire --id example-license --reason 停止维护理由
python3 -B tooling/yunxia.py agreement maintain --id example-license --state active --reason 恢复维护理由
```

revise要求工作正文仍与基线一致，防止覆盖已编辑内容；它绑定下一版目标及基线，清除旧批准，正文随后由维护者修改。已发布正文任何改变都需要新版本。历史引用链必须可从固定Git对象解析，缺失或摘要不一致会使检查失败。

recommend只能指向可推荐的正式版，不填version表示停止推荐。retire清除推荐并停止维护，历史记录仍保留；maintain支持active、paused和retired。维护状态、推荐和适配支持分别管理，不因工具升级或新版本发布改变旧项目绑定。

主动撤回分发使用实际决定文件（date、reason、confirmed_by）：

```bash
python3 -B tooling/yunxia.py agreement withdraw --id example-license --version 1.0.0 --input /private/withdrawal.json
```

撤回保留原版本和发布证据，停止推荐；不能删除正文来表达撤回，也不自动改变既有项目的采用记录或推定其法律效果。

## 适配与历史保留

发布与Skill适配独立。人工采用指南按具体协议编写；adapter登记和真实输入/输出测试决定能否自动生成。登记绑定协议ID、版本及正文摘要，不能按系列名称或版本前缀推断兼容。新增版本支持后重新构建和安装Skill。

三份历史版本保留imported-draft身份、原有名称、说明、正文摘要和空发布事实。迁移绑定现有Git历史中实际存在的releases路径，原source.path仅作为历史来源注记保留；迁移不补造审阅或正式发布。

维护者应保留完整Git历史或可恢复镜像。构建与登记校验需要完整历史以核对历次登记事实；浅克隆先补齐历史，缺少固定对象时明确失败，不得自动改pin、用HEAD替代或重新归一化正文。独立Skill携带构建时的版本与资产，可离线采用，但不能获知构建后的维护变化。
