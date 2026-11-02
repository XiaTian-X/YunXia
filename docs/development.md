# 开发、构建与安装

任务入口见 [任务导航](README.md)。协议新增、审阅、发布与修订按维护指南进行；本页维护工具开发、适配测试和分发安装约定。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r tooling/requirements-dev.txt
make check
make build
make skill-install
```

写入、构建、安装和恢复需要支持POSIX文件锁的系统（如macOS/Linux）；不支持时明确返回FILE_LOCK_UNSUPPORTED，只读目录查询不加载该锁实现。维护与构建需要可取得登记对象的Git仓库；GitHub发布渠道另需已认证的gh。Python核心使用标准库，完整测试的JSON Schema契约检查使用jsonschema。测试只创建临时Git仓库和模拟发布端，不对真实仓库发布协议。

make check检查registry结构、完整来源引用、固定摘要、当前审阅材料、本地链接及回归测试，不自动刷新期望摘要。make build按本次固定输入快照读取Git正文，生成dist/skills/yunxia。BUILD.json覆盖独立包中的文件集合，复制结果须逐项匹配输入；缺少资产、额外文件、符号链接或并发混合输入不能替换前一份构建。

安装前核对构建包与当前来源。安装目标必须在源码仓库之外，不能是源码的上级目录。已有非空目标必须是yunxia Skill。安装器先备份旧目录，替换失败时恢复；若恢复失败，保留并报告备份位置。包内摘要用于一致性核验，来源信任依靠可信维护仓库或经过外部核验的发布渠道。

源码入口是tooling/yunxia.py。核心位于tooling/src/yunxia：common提供严格JSON解析、路径校验、摘要和原子写入；catalog读取registry及固定资产；Git取材层读取确切提交中的普通blob；lifecycle处理设立、审阅、冻结和维护；发布模块处理意图、交付与完成回执；project处理具体项目；adapters包含已测试的适配规则。历史工具不会被执行。

Skill指令来源在skills/yunxia，应安装构建包，不直接安装指令源码目录。构建包包含引擎、登记快照和固定正文，离开源码仓库且没有Git或网络仍可运行。离线目录反映构建时状态，不宣称已获知后来发生的推荐或撤回变化。

新增同类协议适配，先确认已实现的scope-license-v1适合其要求，再登记完整引用和输出名，并补充真实输入/输出回归：

```bash
python3 -B tooling/yunxia.py adapter --agreement AGREEMENT --version VERSION --output-name LICENSE.example-1.txt
```

元数据不能注入可执行代码；其他工作流需实现和验证后接入。登记支持不会改变发布状态。现有项目仍使用状态格式3和三字段采用引用，迁移历史存储不重写采用记录。

测试夹具按当前格式生成真实Git对象。覆盖固定来源、审阅失效、版本更新、发布中断/重试、离线包完整性、多单元采用及项目事务恢复。敏感材料、原始备份、环境、缓存、发布执行临时数据、构建产物和检查报告由.gitignore排除。

发生异常时，先按 [异常与恢复](maintenance.md#recovery) 区分维护锁、发布回执、分发安装和项目事务，再选择对应入口。

维护锁使用保留的`.yunxia-maintenance.guard`文件作为互斥载体，项目使用`.yunxia/lock.guard`；这些文件已被忽略，不是需要提交或删除的缓存。PID记录仍用于判断中断与显式恢复，恢复过程也持有同一进程锁。
