"""Tested scope-license-v1 renderer. Runtime code, never catalog-provided code."""
from pathlib import Path

def render(engine, root, state, mode):
    selected, blockers, adoption_valid = engine.selected, engine.blockers, engine.adoption_valid
    WorkflowError, is_license, md, active_answer = engine.WorkflowError, engine.is_license, engine.md, engine.active_answer
    DRAFT_BASE, OUTPUT_BASE = engine.DRAFT_BASE, engine.OUTPUT_BASE
    agreement = selected(state, mode)
    body = agreement['_text']
    pending = blockers(state)
    if mode == 'adopt':
        if pending:
            raise WorkflowError('ADOPTION_BLOCKED', sorted({p for item in pending for p in item['paths']}))
        if not adoption_valid(state):
            raise WorkflowError('ADOPTION_NOT_CONFIRMED')
    prefix = DRAFT_BASE if mode == 'draft' else OUTPUT_BASE
    previous = state['generations'].get(mode, {}).get('files', {})
    # Preserve an existing project's primary LICENSE and use a separate fixed text.
    license_name = 'LICENSE.txt'
    other_licenses = [p for p in state['snapshot']['files'] if '/' not in p and is_license(Path(p))]
    if other_licenses or agreement['output_name'] in previous:
        license_name = agreement['output_name']
    identity = state['profile'].get('identity', {})
    delivery = state['profile'].get('delivery', {})
    lines = ['# 项目权利与许可范围', '',
             '状态：' + ('草稿；尚未形成采用声明。' if mode == 'draft' else '依据记录中的用户授权生成；权限依据未经独立核验。'), '',
             '项目：' + md(identity.get('project_name', '待确认')),
             '许可主体：' + md(identity.get('licensor', '待确认')),
             '身份：' + md(identity.get('role', '待确认')), '',
             f'协议：{md(agreement["display_name"])}；版本：{md(agreement["version"])}；文本修订：{md(agreement.get("text_revision", "无附加修订"))}；状态：{md(agreement["status"])}。',
             f'协议ID：{md(agreement["agreement_id"])}；固定全文：{license_name}；SHA-256：{agreement["sha256"]}。',
             '交付范围：' + md(delivery.get('description', '待确认')),
             '接收者：' + md(delivery.get('audience', '待确认')), '']
    if mode == 'adopt':
        lines += ['采用日期：' + md(state['adoption']['date']), '',
                  '上述主体依据其确认的权限，将下表“所选协议范围”中的确切内容按上述固定版本授权。',
                  '分别许可和排除项不因本声明改授；原有有效授权、署名和适用条件按其依据保留。', '']
    lines += ['| 文件 | 内容 SHA-256 | 范围 |', '|---|---|---|']
    names = {'selected': '所选协议范围', 'yunxia': '所选协议范围', 'preserve': '保留原许可', 'exclude': '排除于本次授权', 'pending': '待核'}
    third = ['# 第三方与保留声明', '', '以下范围保留原许可及声明；本清单不认证许可兼容或义务已经履行。', '']
    for rel, item in sorted(state['snapshot']['files'].items()):
        a = active_answer(state, rel)
        disposition = a.get('disposition', 'pending')
        lines.append(f'| {md(rel)} | {item["sha256"]} | {names[disposition]} |')
        if disposition == 'preserve':
            third += ['- 对象：' + md(rel), '  声明文件：' + '、'.join(md(p) for p in a['notice_hashes'])]
            if a.get('public_notice'):
                third += ['  公开说明：' + md(a['public_notice'])]
    lines += ['', '范围按列出的内容快照确定。文件名和管理记录不是额外的下游许可条件。',
              '排除项只表示未由本声明授权，不证明其不在交付物中或无需履行其他条件。',
              '扫描覆盖限制与内部证据单独保存；文件整理不能补足缺失的权限。', '']
    if pending:
        lines += ['待核类别：' + '、'.join(p['code'] for p in pending), '']
    outputs = {prefix + license_name: body, prefix + 'PROJECT-RIGHTS.md': '\n'.join(lines).encode()}
    if any(active_answer(state, p).get('disposition') == 'preserve' for p in state['snapshot']['files']):
        outputs[prefix + 'THIRD-PARTY.md'] = ('\n'.join(third) + '\n').encode()
    elif prefix + 'THIRD-PARTY.md' in previous:
        outputs[prefix + 'THIRD-PARTY.md'] = ('\n'.join(third) + '\n当前记录没有列入保留原许可范围；不等于已证明不存在第三方材料。\n').encode()
    ai = state['profile'].get('public_ai_statement')
    if ai:
        outputs[prefix + 'AI-USAGE.md'] = ('# AI 使用说明\n\n' + md(ai) + '\n').encode()
    elif prefix + 'AI-USAGE.md' in previous:
        outputs[prefix + 'AI-USAGE.md'] = '# AI 使用说明\n\n当前未确认可公开的AI使用摘要；不推定未使用AI。\n'.encode()
    return outputs

