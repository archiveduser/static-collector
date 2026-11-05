#!/usr/bin/env python3
"""Collect selected upstream files using only Python's standard library and git."""
import argparse
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def relative(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError(f'Invalid relative path: {value!r}')
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ('.', '..') or p.startswith('.') for p in value.split('/')):
        raise ValueError(f'Unsafe relative path: {value!r}')
    return path


def matches(path, rules):
    # Literal paths select an exact file or an entire directory subtree.
    return any(path == rule or rule in path.parents for rule in rules)


def load_config(path):
    sources = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(sources, list) or not sources:
        raise ValueError('sources must be a non-empty list')
    targets = []
    for source in sources:
        if not isinstance(source.get('enabled', True), bool):
            raise ValueError('enabled must be a boolean')
        if not isinstance(source['url'], str) or not source['url'].startswith('https://'):
            raise ValueError('Upstream URL must use HTTPS')
        branch = source['branch']
        if not isinstance(branch, str) or not branch or branch.startswith('-'):
            raise ValueError('Invalid branch')
        target = relative(source['destination'])
        if any(target in other.parents or other in target.parents for other in targets):
            raise ValueError('Destinations must not be nested')
        targets.append(target)
        for key in ('include', 'exclude'):
            rules = source.get(key, [])
            if not isinstance(rules, list):
                raise ValueError(f'{key} must be a list')
            source[key] = [relative(rule) for rule in rules]
    return sources


def copy_selected(source, destination, include, exclude):
    count = 0
    for item in sorted(source.rglob('*')):
        path = PurePosixPath(item.relative_to(source).as_posix())
        if any(part.startswith('.') for part in path.parts):
            continue
        if item.is_symlink() or any(parent.is_symlink() for parent in item.parents if parent != source):
            continue
        if not item.is_file() or matches(path, exclude):
            continue
        if include and not matches(path, include):
            continue
        target = destination.joinpath(*path.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'sources.json')
    args = parser.parse_args()
    sources = load_config(args.config)
    # Stage each source independently; publish only successfully collected sources.
    with tempfile.TemporaryDirectory(prefix='collect-', dir=ROOT) as temporary:
        work = Path(temporary)
        output = work / 'dist'
        output.mkdir()
        collected = []
        for index, source in enumerate(sources):
            if not source.get('enabled', True):
                print(f"{source['destination']}: disabled, skipped", flush=True)
                continue
            checkout = work / str(index)
            staged = work / f'output-{index}'
            try:
                subprocess.run(['git', 'clone', '--depth=1', '--single-branch', '--branch', source['branch'], '--', source['url'], str(checkout)], check=True)
                staged.mkdir()
                count = copy_selected(checkout, staged, source['include'], source['exclude'])
                collected.append((staged, source['destination']))
                print(f"{source['destination']}: {count} files", flush=True)
            except (subprocess.CalledProcessError, OSError) as error:
                print(f"{source['destination']}: failed, skipped: {error}", flush=True)
        # Merge successful sources sharing a destination. A merge error aborts
        # before replacing dist; later sources replace files with identical paths.
        for staged, target in collected:
            destination = output / target
            if destination.exists():
                shutil.copytree(staged, destination, dirs_exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                staged.rename(destination)
        dist = ROOT / 'dist'
        backup = work / 'previous-dist'
        if dist.exists() or dist.is_symlink():
            dist.rename(backup)
        try:
            output.rename(dist)
        except OSError:
            if backup.exists() or backup.is_symlink():
                backup.rename(dist)
            raise


if __name__ == '__main__':
    main()
