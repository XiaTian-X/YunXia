# 开发、构建与安装

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r tooling/requirements-dev.txt
make test
make check
make build
make skill-install
```

make check只读检查元数据、固定摘要、本地链接及回归测试，不自动刷新期望摘要。make build从协议目录与同一套引擎源码生成dist/skills/yunxia；BUILD.json覆盖所有交付文件，安装前重新校验。安装目标必须位于源码仓库之外，并且不能是源码的上级目录。已有非空目标必须是yunxia Skill，避免误替换源码或无关文件。安装器先备份旧目录，替换失败时恢复；若恢复失败，保留并报告备份位置。

源码入口为tooling/yunxia.py；核心位于tooling/src/yunxia。common提供独立的JSON、路径和摘要函数；catalog读取固定版本，lifecycle负责维护动作，project处理具体项目，adapters包含已测试的规则。Skill指令来源在skills/yunxia；不直接安装指令源码目录，应安装构建产物。构建包包含自己的代码和固定资产，离开源码仓库仍可运行。

新增同类协议适配使用adapter命令登记完整引用与输出名，并补充真实输入/输出回归。元数据只能选择已实现的scope-license-v1，不能注入可执行脚本；其他工作流需要实现并验证后接入。支持登记不会改变发布状态。

```bash
python3 -B tooling/yunxia.py adapter --agreement AGREEMENT --version VERSION --output-name LICENSE.example-1.txt
```

测试在临时目录按当前格式创建虚构协议和项目，覆盖正常流程、边界和故障恢复，不作为实际协议发布。旧目录读取器、旧资产登记工具与样本已移除。项目采用统一使用状态格式3和三字段协议引用。完整测试中的JSON Schema契约检查使用jsonschema，运行引擎本身不依赖它。

构建产物、环境、缓存、维护锁、项目私有状态和常见凭证由.gitignore排除。发布包放在仓库外；内部证据和密钥不自动进入公开材料。

维护命令异常中断留下锁时，可用`python3 -B tooling/yunxia.py recover-maintenance`回收不存在进程的锁；活跃或格式损坏的锁不会被自动删除。
