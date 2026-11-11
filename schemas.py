"""Versioned restaurant ontologies. Local configuration, never executable prompts."""
import json
from pathlib import Path

PATH = Path(__file__).parent / 'data/schemas.json'
PROFILE_PATH = Path(__file__).parent / 'data/domain-profile.json'
DEFAULT = json.loads(PROFILE_PATH.read_text()).get('default_schema_id', 'apparel_traceability') if PROFILE_PATH.exists() else 'apparel_traceability'


def domain_profile():
    if PROFILE_PATH.exists():
        return json.loads(PROFILE_PATH.read_text())
    return {'id': DEFAULT, 'name': '垂直证据知识库', 'industry': 'generic', 'answer_scope': '仅依据已核验资料回答。'}


def catalog():
    return json.loads(PATH.read_text())


def for_document(doc):
    key = doc.get('schema_id') or {'S2': 'food_waste', 'S3': 'licensing'}.get(doc.get('id'), DEFAULT)
    return next((s for s in catalog() if s['id'] == key), catalog()[0])


def validate(schema):
    if not isinstance(schema, dict): raise ValueError('场景配置须为 JSON 对象')
    import re
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', schema.get('id', '')): raise ValueError('场景 ID 须为英文小写、数字或下划线')
    if not isinstance(schema.get('name'), str) or not 1 <= len(schema['name']) <= 50: raise ValueError('场景名称须为 1–50 字')
    terms = schema.get('entity_types')
    if not isinstance(terms, dict) or not 1 <= len(terms) <= 30: raise ValueError('须配置 1–30 个实体类型')
    for kind, values in terms.items():
        if not isinstance(kind, str) or not 1 <= len(kind) <= 30 or not isinstance(values, list) or len(values) > 300: raise ValueError('实体词典格式无效')
        if any(not isinstance(v, str) or not 1 <= len(v) <= 100 for v in values): raise ValueError('词典项须为 1–100 字')
    predicates = schema.get('relations')
    if not isinstance(predicates, list) or not 1 <= len(predicates) <= 40 or any(not isinstance(v, str) or not 1 <= len(v) <= 30 for v in predicates): raise ValueError('关系标签格式无效')
    aliases = schema.get('aliases', {})
    if not isinstance(aliases, dict) or len(aliases) > 300 or any(not isinstance(k, str) or not isinstance(v, str) or not k or not v or len(k) > 100 or len(v) > 100 for k, v in aliases.items()): raise ValueError('同义词格式无效')
    return dict(id=schema['id'], name=schema['name'], description=str(schema.get('description', ''))[:300], entity_types=terms, relations=list(dict.fromkeys(predicates)), aliases=aliases, version=int(schema.get('version', 1)))


def save(schema):
    clean = validate(schema)
    rows = catalog()
    previous = next((s for s in rows if s['id'] == clean['id']), None)
    clean['version'] = previous.get('version', 1) + 1 if previous else 1
    rows = [clean if s['id'] == clean['id'] else s for s in rows]
    if not previous: rows.append(clean)
    tmp = PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    tmp.replace(PATH)
    return clean
