# 状态与事务恢复

本工具仅支持状态格式 3。main单元的状态位于目标项目`.yunxia/state.json`；命名单元位于`.yunxia/units/<id>/state.json`。状态保存扫描快照、用户回答、采用确认、生成文件摘要和历史，采用引用统一包含agreement_id、version和sha256。

只读扫描不创建状态。状态不受支持或字段损坏时明确报错，保留原文件；工具不提供旧状态迁移，也不通过初始化空记录猜测已有授权。有效的当前格式记录可以跨工具版本保留，不因工具升级自动换协议或重复确认。

## 中断恢复

生成材料时，事务日志记录待写文件的原字节和目标字节。中断后出现RECOVERY_REQUIRED，先保留现场，再对同一个单元运行：

```bash
python3 -B ENGINE project --root /project recover
python3 -B ENGINE project --root /project --unit code recover
```

ENGINE表示安装包内scripts/yunxia.py的绝对路径。main和code是两个示例，选择实际中断的单元，不必全部执行。

recover将未完成的这次写入恢复到操作前一致状态，随后重新扫描和检查再继续。不存在事务时返回recovered=false。恢复不会改写协议条款或代替采用确认。

## 并发和人工修改

各单元共用项目写锁。恢复只回收对应进程已不存在的锁；活跃进程的锁不会被删除。若文件在中断后被人工修改，返回RECOVERY_CONFLICT并保留日志，避免覆盖新内容。不要删状态、锁或事务文件强行绕过检查。

历史生成物的字节与摘要不一致时，工具保留原记录并报告；应根据可核验材料处理，不能改摘要伪装修复。

`.yunxia/`和内部回答不进入公开交付物。多单元交付通过verify-all检查，尚未生成采用材料的单元会单独列出。
