# 命令与输入

下面的 `ENGINE` 表示安装包内 `scripts/yunxia.py` 的绝对路径；源码开发使用 `tooling/yunxia.py`。安装包不依赖源码仓库。

```bash
python3 -B ENGINE project catalog
python3 -B ENGINE project --root /project scan --read-only
python3 -B ENGINE project --root /project scan
python3 -B ENGINE project --root /project select --agreement yunxia-license --version 0.9.3-draft.2
python3 -B ENGINE project --root /project questions
python3 -B ENGINE project --root /project answer --input /private/answers.json
python3 -B ENGINE project --root /project generate --mode draft --write
python3 -B ENGINE project --root /project generate --mode adopt --write
python3 -B ENGINE project --root /project verify --artifact /delivery/project.zip
```

命令返回 JSON。退出码 0 表示该命令完成，2 表示结构或业务条件不满足；IO 错误可能为 1。预览不写材料。回答输入放在 `.yunxia/` 或项目外私有临时目录，避免被扫描成项目资产。

## 回答结构

```json
{
  "confirmed_by": "实际确认者",
  "basis": "内部确认依据",
  "identity": {"project_name": "实际项目", "licensor": "实际许可主体", "role": "实际身份"},
  "delivery": {"audience": "实际接收者", "description": "实际交付方式"},
  "assets": [{"paths": ["src/main.py"], "disposition": "selected", "origin": "human", "basis": "实际来源依据"}],
  "adoption": {"authorized": true, "accept_draft": true, "date": "2026-10-25", "snapshot_id": "使用scan返回的值", "agreement": {"agreement_id": "yunxia-license", "version": "0.9.3-draft.2", "sha256": "使用catalog返回的完整摘要"}}
}
```

示例不是事实或授权。资产 origin 可为 human、ai、mixed、third_party、unknown；disposition 可为 selected、preserve、exclude、pending。unknown 不能直接确认 selected。preserve 需要 `license_files` 指向扫描范围内的原声明，可附 `public_notice`。现有许可证或署名文件改授需要明确 `replace_existing_license: true`，不能从普通扫描推定。身份、交付和来源事实按实际情况提供，不要求把每个字段都一次补齐。

`public_ai_statement`仅用于已经确认可以公开的摘要；内部basis不进入公开材料。`defer: ["identity", "delivery", "adoption"]`表示未知或暂缓，没有授权效果。采用引用与scan快照必须匹配。正式版可使用accept_draft=false；草案需真实接受其草案状态。

## 同一项目的多个采用范围

先设置范围，再扫描和选择；路径是项目相对文件或目录，不接受通配符和私有目录。目录下新增内容会在下次扫描进入待核。

```bash
python3 -B ENGINE project --root /project --unit code scope --path src
python3 -B ENGINE project --root /project --unit code scan
python3 -B ENGINE project --root /project --unit code select --agreement yunxia-license --version 0.9.3-draft.2
python3 -B ENGINE project --root /project --unit code questions
```

后续answer、generate、verify都携带同一`--unit code`。其他单元同理。main保留旧根目录输出；命名单元输出在`licenses/code/`，其正文与声明集中可读。所有单元状态均保存在`.yunxia/`，共用写锁；改一个单元不改另一个单元的采用记录。范围缩小后先重新scan，再确认并生成更新的声明；原路径的历史回答保留，但不再阻止其转入其他单元。旧声明尚未更新时verify-all仍报告过期，不能据新单元通过就宣称全部材料一致。

`units`列出范围和绑定；`verify-all --artifact /delivery/project.zip`合并核对所有已生成采用材料的单元。未生成采用材料的计划或草稿单元单独列在pending_units中，不阻止对其他已有采用材料的验证；all_units_ready只有全部单元就绪且检查通过才为true。所有单元都未生成采用材料时不会返回成功。计划单元的扫描结果不会把交付文件自动计为已经核对，产物中没有被已采用单元覆盖的文件仍报告缺口。单元verify只验证该单元，不应当作整个项目的验证。

## 更新和保护

文件变化使相关事实或采用快照失效；目录推荐、工具更新不自动改绑定。生成记录保存适配器标识、实现摘要和工具版本。旧记录缺少适配器信息时报告legacy-unrecorded，不补造过去使用了新适配器；更新生成物时形成新的记录。

手改或缺失的旧输出会阻止覆盖和错误归档。计划后并发修改同样阻止写入。发现历史归档损坏时保留原记录，不能改摘要伪装修复。锁或中断事务按[迁移与恢复](state-migration.md)处理。
