# 协议全周期维护

以下命令在仓库根目录执行；维护其他仓库可使用agreement后面的--repository。工作信息在每个agreement.json维护，固定版本来自release.json，目录查询自动汇总。

## 设立与起草

```bash
python3 -B tooling/yunxia.py agreement create --id example-license --name 示例协议 --purpose 实际设立目的 --version 1.0.0 --text /private/initial-text.txt
```

命令只建立草案，不发布。维护者补齐README中的定位、design中的设计依据、review中的问题和adoption中的人工采用路径。日常直接编辑工作内容，Git保留变化；并行修订用分支。create生成的待填写说明不应被当作完成审阅。

## 审阅与冻结

```bash
python3 -B tooling/yunxia.py agreement inspect --id example-license
python3 -B tooling/yunxia.py agreement review --id example-license --input /private/review.json
python3 -B tooling/yunxia.py agreement freeze --id example-license --version 1.0.0-rc.1 --notes /private/notes.md
```

review.json必须有source_sha256（使用inspect结果）、reviewer、date、decision（approved或changes-required）、basis、open_issues。approved不能带未解决事项。source_sha256同时绑定正文、定位、设计、审阅和采用指南，以及存在时的cases.json、templates/文件和修订基线；这些内容变化后旧批准不再有效。格式缺损的审阅记录会在冻结前被拒绝，不生成不可用的固定候选。工具记录维护者提供的决定，不从测试通过自动生成决定。

freeze只接受有效审阅，生成不可覆盖的候选快照。正文、采用指南和审阅记录按摘要固定；不会自动改写标题、版本号或条款。需要更改时重新审阅并用新候选标识。候选标识必须与target_version不同，正式版本号不能被候选占用；已存在的目标版本不能重复用于新修订。

## 发布

```bash
python3 -B tooling/yunxia.py agreement inspect --id example-license
python3 -B tooling/yunxia.py agreement publish --id example-license --candidate 1.0.0-rc.1 --version 1.0.0 --destination /outside/releases/example-license-1.0.0 --input /private/publication.json
```

publication.json必须有date、confirmed_by、basis和candidate_sha256（inspect的fixed_versions返回package_sha256）。正式版本必须等于候选来源中已审阅的target_version。工具将确切文件交付至指定仓库外目录，写入后重新读取并核对交付内容，再记录released及真实本地路径；交付被修改时保留现场并报告，不能宣称已验证交付。目标已有不同内容时拒绝覆盖；若交付完成但登记失败，保留交付并报告，使用相同输入重试恢复登记。

此实现完成本地目录发布。它不会上传GitHub、部署网站或宣称远程URL已可访问；对外托管可以使用该目录作为待发布产物，远程交付应另行验证。本仓库当前没有正式发布记录，本次重构不执行上述publish操作。

## 更新与停止推荐

```bash
python3 -B tooling/yunxia.py agreement revise --id example-license --base 1.0.0 --version 2.0.0
python3 -B tooling/yunxia.py agreement recommend --id example-license --version 1.0.0 --reason 实际推荐理由
python3 -B tooling/yunxia.py agreement recommend --id example-license --reason 暂停推荐的实际理由
python3 -B tooling/yunxia.py agreement retire --id example-license --reason 停止维护的实际理由
python3 -B tooling/yunxia.py agreement maintain --id example-license --state active --reason 恢复维护的实际理由
```

revise要求工作正文仍与所选基线一致，防止覆盖正在编辑的内容；它只调整下一版维护信息，随后由维护者编辑正文。旧发布文件不变。recommend只能指向正式版，不填version表示停止推荐；retire清除推荐并标记停止维护，历史版本仍可取得。maintain支持active、paused和retired；暂停或停止维护后，须明确恢复active才能继续审阅、冻结或发布。

发布正文任何改变都需要新协议版本。指南修正或适配器修复随文档/工具更新，不能悄悄替换旧正文；模板和工具约定不会成为新增许可条件。

## 适配支持

发布与工具适配独立。人工采用指南按该协议真实要求编写；adapter登记与测试决定Skill能否生成。目标项目保存确切版本及采用范围，推荐项变化不改变旧项目。旧草案保留为imported-draft，不能被recommend当成正式版。
