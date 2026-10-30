# 旧状态与恢复

当前状态格式3显式记录采用单元身份，接受新三字段和旧四字段协议引用。旧格式1/2用`project --root /project migrate-state`迁移；普通命令发现旧格式时报告STATE_MIGRATION_REQUIRED，不暗中改写。默认main读取原`.yunxia/state.json`，原回答、采用、生成基线和历史不因升级重置。命名单元在`.yunxia/units/<id>/state.json`独立保存范围与记录。

原四字段引用（agreement_id、version、text_revision、sha256）通过固定快照中的legacy_references逐项解析。旧采用事件原样保留，不偷偷替换成新三字段引用。找不到原文本或摘要不符时停止相关生成，不能用推荐文本代替。

格式2迁移保留原采用事件、四字段引用、回答和生成基线，并保存整个原始状态字节；迁移后不重复询问已经充分确认的事项。只更新内部格式，不改公开文件。

状态1使用显式`project --root /project migrate-state`。工具保存原始状态字节及旧采用信息，保留回答与输出，但不猜测旧记录缺少的协议身份。根据真实证据重新绑定；已有充分任务授权可复用，无须机械重复提问。

出现RECOVERY_REQUIRED时，先保留现场并运行同一单元的recover。恢复只回收不存在进程的锁；人工修改形成RECOVERY_CONFLICT时不能覆盖。不要删除状态、锁或事务来强行继续。

每个单元的事务覆盖该单元状态与材料，项目写锁保护单元间并发操作。完整交付使用verify-all核对；内部`.yunxia/`和回答文件不进入公开材料。
