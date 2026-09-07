"""一次运行一套证据；阶段失败可观察，恢复时只复用已完成阶段。"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .harness import Cost


def write_json(path: Path, value) -> None:
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)


def code_fingerprint(root: Path) -> str:
    files = sorted((root / 'paperscout').glob('*.py')) + sorted((root / 'runtime').glob('*.mjs'))
    files += [root / 'run_pipeline.py', root / 'patches/aminer.patch.yml', root / 'requirements.txt']
    files += sorted((root / 'config/sdk').iterdir())
    return hashlib.sha256(b''.join(p.read_bytes() for p in files)).hexdigest()


class RunStore:
    def __init__(self, path: Path):
        self.path = path
        self.manifest = json.loads((path / 'manifest.json').read_text())
        self.lock = threading.Lock()

    @classmethod
    def create(cls, base: Path, plan: dict, fingerprint: str):
        name = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
        path = base / name
        path.mkdir(parents=True)
        write_json(path / 'plan.json', plan)
        write_json(path / 'manifest.json', {'run_id': name, 'fingerprint': fingerprint,
                   'plan_hash': hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest(),
                   'status': 'running', 'stages': {}, 'cost_scope': '本运行内所有已记录尝试；外部 plan 的生成成本不包含'})
        return cls(path)

    def stage(self, name: str, operation):
        with self.lock:
            entry = self.manifest['stages'].get(name, {})
            if entry.get('status') == 'complete':
                print(f'[{name}] 复用已完成阶段', flush=True)
                return json.loads((self.path / f'{name}.json').read_text())
            self.manifest['stages'][name] = {'status': 'running', 'attempts': entry.get('attempts', 0) + 1}
            write_json(self.path / 'manifest.json', self.manifest)
        print(f'[{name}] 开始', flush=True)
        try:
            value = operation()
            write_json(self.path / f'{name}.json', value)
        except Exception as error:
            # 阶段边界记录后重抛；是否交付部分报告由主流程明确决定。
            with self.lock:
                self.manifest['stages'][name].update(status='failed', error=f'{type(error).__name__}: {error}')
                write_json(self.path / 'manifest.json', self.manifest)
            raise
        with self.lock:
            self.manifest['stages'][name].update(status='complete')
            self.manifest['stages'][name].pop('error', None)
            write_json(self.path / 'manifest.json', self.manifest)
        return value

    def invalidate(self, names: list[str]) -> None:
        for name in names:
            self.manifest['stages'].pop(name, None)
        write_json(self.path / 'manifest.json', self.manifest)

    def cost(self) -> tuple[Cost, int]:
        total, unknown = Cost(), 0
        for path in (self.path / 'agents').glob('*.json'):
            record = json.loads(path.read_text())
            if 'cost' in record:
                total.merge(Cost(**record['cost']))
            else:
                unknown += 1
        return total, unknown

    def finish(self, status: str) -> dict:
        cost, unknown = self.cost()
        self.manifest.update(status=status, cost=asdict(cost), unknown_cost_attempts=unknown)
        write_json(self.path / 'manifest.json', self.manifest)
        return self.manifest
