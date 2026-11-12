"""Import the installed official implementation without writing into its directory."""

import hashlib
import importlib
import importlib.util
import json
import sys
from pathlib import Path


def load_default():
    contract = json.loads(Path('res/default_contract.json').read_text(encoding='utf8'))
    root = Path('/opt/booster/booster_agent_data/data/agents/extract') / contract['id']
    manifest = json.loads((root / 'agent.json').read_text(encoding='utf8'))
    if manifest['version'] != contract['version'] or manifest.get('skill_refs') != contract['skill_refs']:
        raise RuntimeError('Official default Agent changed; revalidate this extension before use')
    module = root / 'agent/com_boosterobotics_default/lib/python3.10/site-packages/com_boosterobotics_default'
    for name, expected in contract['files'].items():
        if hashlib.sha256((module / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'Official default implementation differs: {name}; revalidation required')
    name = '_k1_music_original_default'
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(name, module / '__init__.py',
                                                    submodule_search_locations=[str(module)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[name] = package
        spec.loader.exec_module(package)
        return importlib.import_module(name + '.default_agent').DefaultAgent
    finally:
        sys.dont_write_bytecode = previous
