#!/usr/bin/env python3
"""Upload collected resources and report errors without exposing COS settings."""
import configparser
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
SETTINGS = ('SECRET_ID', 'SECRET_KEY', 'COS_BUCKET', 'COS_REGION')


def redact(message, settings):
    # Hide URLs before replacing their components, including credential encodings.
    message = re.sub(r'https?://[^\s<>\x22\x27]+', '[URL redacted]', message)
    values = set(settings.values())
    bucket = settings.get('COS_BUCKET', '')
    if '-' in bucket:
        values.add(bucket.rsplit('-', 1)[0])
    for value in sorted(filter(None, values), key=len, reverse=True):
        message = message.replace(value, '[redacted]')
        message = message.replace(quote(value, safe=''), '[redacted]')
    # Prevent subprocess output from being interpreted as workflow commands.
    return '\n'.join('  ' + line for line in message.splitlines())


def main():
    settings = {name: os.environ.get(name, '').strip() for name in SETTINGS}
    missing = [name for name, value in settings.items() if not value]
    if missing:
        print('Missing GitHub Actions secrets: ' + ', '.join(missing), flush=True)
        return 1
    if any('\n' in value or '\r' in value for value in settings.values()):
        print('COS secrets must be single-line values.', flush=True)
        return 1
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*-\d+', settings['COS_BUCKET']):
        print('COS_BUCKET must be a bucket name including its numeric APPID suffix, without a URL.', flush=True)
        return 1
    config = configparser.ConfigParser(interpolation=None)
    config['common'] = dict(
        secret_id=settings['SECRET_ID'], secret_key=settings['SECRET_KEY'],
        bucket=settings['COS_BUCKET'], region=settings['COS_REGION'],
        max_thread='5', part_size='1', retry='5', timeout='60',
        schema='https', verify='md5', anonymous='False',
    )
    with tempfile.TemporaryDirectory(prefix='cos-upload-') as directory:
        config_path = Path(directory) / 'cos.conf'
        with config_path.open('w', encoding='utf-8') as stream:
            config.write(stream)
        config_path.chmod(0o600)
        with tempfile.TemporaryFile(mode='w+b') as log:
            result = subprocess.run(
                ['coscmd', '-c', str(config_path), 'upload', '-rs', './dist/', '/'],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
            )
            if result.returncode:
                print(f'COS upload failed (exit {result.returncode}). Sanitized output:', flush=True)
                log.seek(0)
                output = redact(log.read().decode('utf-8', errors='replace'), settings)
                print(output[-16000:] or '  No diagnostic output was produced.', flush=True)
                return 1
    print('COS upload succeeded.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
