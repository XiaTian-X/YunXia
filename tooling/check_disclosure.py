#!/usr/bin/env python3
"""Offline record consistency checks; never a license or legal decision."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
COLLECTIONS = {
    'assets': 'asset_id', 'ai_events': 'event_id',
    'runtime_services': 'service_id', 'terms_records': 'terms_id',
    'obligations': 'obligation_id', 'evidence_records': 'evidence_id',
    'change_log': 'change_id',
}
REFERENCES = {
    'asset_ids': 'assets', 'ai_event_ids': 'ai_events', 'event_ids': 'ai_events',
    'service_ids': 'runtime_services', 'terms_record_ids': 'terms_records',
    'obligation_ids': 'obligations', 'evidence_ids': 'evidence_records',
    'authority_evidence_ids': 'evidence_records', 'input_source_evidence_ids': 'evidence_records',
    'basis_evidence_ids': 'evidence_records',
}
LIMIT = '仅检查结构、引用及部分状态一致性；不读取证据、不联网、不作权属、兼容、完整性或发布可行性判断。'


def walk(value, path='$'):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            if key in ('legacy_record', 'rights_detail', 'field_guide'):
                continue
            yield from walk(child, path + '.' + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f'{path}[{index}]')


def check(data):
    from jsonschema import Draft202012Validator
    version = data.get('schema_version') if isinstance(data, dict) else None
    schema_path = 'tooling/schemas/asset-register-2.0.0.schema.json' if version == '2.0.0' else 'tooling/schemas/asset-register-1.0.0.schema.json'
    schema = json.loads((ROOT / schema_path).read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(schema)
    errors, notes = [], []

    def issue(code, path, detail):
        errors.append({'code': code, 'path': path, 'detail': detail})

    for error in Draft202012Validator(schema).iter_errors(data):
        path = '$' + ''.join(f'[{x}]' if isinstance(x, int) else '.' + x for x in error.absolute_path)
        # Do not echo field values: the input can contain confidential information.
        issue('SCHEMA', path, '字段不符合格式约束：' + str(error.validator))
    if errors:
        return {'record_structure_valid': False, 'errors': errors, 'notes': [], 'legal_assessment': 'not_performed', 'release_assessment': 'not_performed', 'limit': LIMIT}

    indices, all_ids = {}, set()
    for group, id_key in COLLECTIONS.items():
        indices[group] = {}
        for i, item in enumerate(data[group]):
            ident = item[id_key]
            if ident in all_ids:
                issue('DUPLICATE_ID', f'$.{group}[{i}].{id_key}', '记录编号重复。')
            all_ids.add(ident)
            indices[group][ident] = item
    # Statements share the same identifier namespace.
    for path, item in walk(data):
        if 'statement_id' in item:
            ident = item['statement_id']
            if ident in all_ids:
                issue('DUPLICATE_ID', path + '.statement_id', '记录编号重复。')
            all_ids.add(ident)

    for path, item in walk(data):
        for key, group in REFERENCES.items():
            if key in item:
                for i, ident in enumerate(item[key]):
                    if ident not in indices[group]:
                        issue('DANGLING_REFERENCE', f'{path}.{key}[{i}]', '引用目标在本登记中不存在。')
        if 'affected_ids' in item:
            for i, ident in enumerate(item['affected_ids']):
                if ident not in all_ids:
                    issue('DANGLING_REFERENCE', f'{path}.affected_ids[{i}]', '引用目标在本登记中不存在；根级变更可使用空列表并说明范围。')
        if item.get('status') == 'checked':
            for key in ('scope', 'reviewer', 'reviewed_at', 'evidence_ids'):
                if not item.get(key):
                    issue('CHECKED_WITHOUT_RECORD', path + '.' + key, '已核对状态需记录范围、人员、日期及证据引用。')
        if item.get('decision') in ('conditional', 'independent_basis'):
            for key in ('target_use', 'scope', 'basis_evidence_ids', 'reason', 'assessed_by', 'assessed_at'):
                if not item.get(key):
                    issue('DECISION_WITHOUT_BASIS', path + '.' + key, '使用路径决定缺少对应依据或范围。')
            if item['decision'] == 'conditional' and not item['conditions']:
                issue('CONDITIONS_MISSING', path + '.conditions', '按所列条件可用需列出条件；是否充分仍由人工核验。')
        if 'model_version' in item and (item.get('model') is None or item['model_version'] is None):
            if not item['unknown_reason']:
                issue('UNKNOWN_REASON_MISSING', path + '.unknown_reason', '型号或版本未知时说明原因。')
        if 'statement_id' in item:
            if item['status'] != 'draft':
                for key in ('locations', 'reviewed_by', 'reviewed_at'):
                    if not item[key]:
                        issue('PUBLICATION_RECORD_MISSING', path + '.' + key, '公开、已更正或历史状态需保存位置及核对记录。')
            if item['kind'] != 'voluntary' and not item['obligation_ids']:
                issue('STATEMENT_BASIS_MISSING', path + '.obligation_ids', '非自愿声明须关联实际依据。')
            for ident in item['obligation_ids']:
                obligation = indices['obligations'].get(ident)
                if obligation and obligation['source_kind'] != item['kind']:
                    issue('STATEMENT_KIND_MISMATCH', path + '.obligation_ids', '声明与其义务来源类别不一致；不同类别分别记录。')

    # Bidirectional links keep an asset's public scope aligned with event/obligation scope.
    for left_group, left_key, right_group, right_key in (
        ('assets', 'ai_event_ids', 'ai_events', 'asset_ids'),
        ('assets', 'obligation_ids', 'obligations', 'asset_ids'),
        ('runtime_services', 'obligation_ids', 'obligations', 'service_ids'),
    ):
        for a_group, a_key, b_group, b_key in ((left_group,left_key,right_group,right_key),(right_group,right_key,left_group,left_key)):
            for i, item in enumerate(data[a_group]):
                ident = item[COLLECTIONS[a_group]]
                for target_id in item[a_key]:
                    target = indices[b_group].get(target_id)
                    if target and ident not in target[b_key]:
                        issue('SCOPE_LINK_MISMATCH', f'$.{a_group}[{i}].{a_key}', '对象与事件或义务范围的双向引用不一致。')

    for i, obligation in enumerate(data['obligations']):
        path = f'$.obligations[{i}]'
        applicability, fulfillment = obligation['applicability'], obligation['fulfillment']
        if applicability != 'pending':
            required = ('source_title', 'source_locator', 'clause', 'actor', 'trigger', 'applicability_reason')
            for key in required:
                if not obligation[key]:
                    issue('OBLIGATION_BASIS_MISSING', path + '.' + key, '已判定触发或未触发的事项需说明具体依据和事实。')
            if obligation['source_kind'] not in ('internal', 'voluntary') and not obligation['basis_evidence_ids']:
                issue('OBLIGATION_BASIS_MISSING', path + '.basis_evidence_ids', '外部义务的适用结论需有依据引用。')
        if obligation['source_kind'] in ('voluntary', 'internal') and obligation['downstream_requirement_basis']:
            issue('VOLUNTARY_AS_DOWNSTREAM_RULE', path + '.downstream_requirement_basis', '自愿或内部事项不能在本格式中同时作为下游强制条件。')
        status = fulfillment['status']
        if (status == 'not_triggered') != (applicability == 'not_triggered'):
            issue('TRIGGER_STATUS_MISMATCH', path + '.fulfillment.status', '未触发与履行状态不一致。')
        if status in ('fulfilled', 'remediation') and applicability != 'triggered':
            issue('TRIGGER_STATUS_MISMATCH', path + '.fulfillment.status', '履行或补正状态需对应已触发事项。')
        if status == 'fulfilled':
            for key in ('checked_by', 'checked_at', 'evidence_ids', 'result'):
                if not fulfillment[key]:
                    issue('FULFILLMENT_RECORD_MISSING', path + '.fulfillment.' + key, '已履行需实际检查记录和证据引用。')
            if not obligation['delivery_locations'] or obligation['audience'] == 'pending':
                issue('DELIVERY_LOCATION_MISSING', path, '已履行需定位接收者和实际履行位置。')

    if not data['assets']:
        notes.append({'code':'EMPTY_ASSET_REGISTER','detail':'资产登记为空；不能推断无第三方内容或全部可用。'})
    if data['document_status'] != 'project-record':
        notes.append({'code':'NOT_PROJECT_FACTS','detail':'这是模板或假设示例，不是实际项目授权记录。'})
    adopted = data['adopted_agreement'] if version == '2.0.0' else data['adopted_license_version']
    if not adopted or not data['adoption_date']:
        notes.append({'code':'ADOPTION_NOT_RECORDED','detail':'实际采用记录尚不完整，检查工具不补填授权事实。'})
    if bool(adopted) != bool(data['adoption_date']):
        issue('ADOPTION_PAIR_INCOMPLETE', '$', '采用版本与实际日期应按同一真实事件记录；不确定时均保留待核。')
    counts = {
        'registered_assets': len(data['assets']),
        'runtime_services': len(data['runtime_services']),
        'asset_reviews_marked_checked': sum(a['review']['status']=='checked' for a in data['assets']),
        'asset_paths_pending': sum(a['use_path']['decision']=='pending' for a in data['assets']),
        'obligations_pending_applicability': sum(o['applicability']=='pending' for o in data['obligations']),
        'obligations_pending_fulfillment': sum(o['applicability']=='triggered' and o['fulfillment']['status']!='fulfilled' for o in data['obligations']),
    }
    notes.append({'code':'HUMAN_SCOPE_REVIEW_REQUIRED','detail':'计数只反映输入记录；证据真实性、清单完整性、实际交付和法律判断未检查。'})
    return {'record_structure_valid':not errors,'counts_from_input':counts,'errors':errors,'notes':notes,
            'legal_assessment':'not_performed','release_assessment':'not_performed','limit':LIMIT}


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('JSON contains duplicate keys')
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(description=LIMIT)
    parser.add_argument('record', type=Path, help='内部记录 JSON 文件')
    args = parser.parse_args()
    try:
        data = json.loads(args.record.read_text(encoding='utf-8'), object_pairs_hook=unique_keys)
        result = check(data)
    except ImportError:
        print(json.dumps({'error':'缺少 jsonschema；按 tools/README.md 安装可选检查依赖。'},ensure_ascii=False))
        return 1
    except (OSError, ValueError):
        print(json.dumps({'error':'无法读取有效 JSON；请检查文件、编码、语法及重复键。'},ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['record_structure_valid'] else 2


if __name__ == '__main__':
    sys.exit(main())
