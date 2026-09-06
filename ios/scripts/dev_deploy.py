#!/usr/bin/env python3
"""Build once; deploy immutable signed artifacts to reachable physical iPhones."""
import argparse
import concurrent.futures
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'build' / 'dev-deploy'
BUNDLE = 'com.arulandu.SignalMap'
XCODE = os.environ.get('DEVELOPER_DIR', '/Applications/Xcode 26.3.app/Contents/Developer')
ENV = dict(os.environ, DEVELOPER_DIR=XCODE)


def say(message):
    print(f'[{time.strftime("%H:%M:%S")}] {message}', flush=True)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as f:
        json.dump(value, f, indent=2)
        temporary = f.name
    os.replace(temporary, path)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


@contextlib.contextmanager
def lock(name, nonblocking=False):
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / name).open('a+') as f:
        fcntl.flock(f, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
        try:
            yield f
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def execute(args, *, timeout=90, env=ENV, logfile=None):
    if logfile:
        with logfile.open('w') as output:
            result = subprocess.run(args, cwd=ROOT, env=env, stdout=output,
                                    stderr=subprocess.STDOUT, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f'Command failed; see {logfile}\n{logfile.read_text()[-1800:]}')
        return ''
    result = subprocess.run(args, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stdout.strip()[-1800:])
    return result.stdout


def device_command(args, timeout=30):
    with tempfile.TemporaryDirectory(prefix='signalmap-dev-') as folder:
        path = Path(folder) / 'result.json'
        execute(['xcrun', 'devicectl', *args, '--timeout', str(timeout),
                 '--json-output', str(path)], timeout=timeout + 5)
        data = read_json(path, {})
        if data.get('info', {}).get('outcome') != 'success':
            raise RuntimeError(f'Device command did not report success: {data}')
        return data.get('result', {})


def eligible(device):
    hardware = device.get('hardwareProperties', {})
    connection = device.get('connectionProperties', {})
    capabilities = {c.get('featureIdentifier') for c in device.get('capabilities', [])}
    return (hardware.get('deviceType') == 'iPhone' and hardware.get('reality') == 'physical'
            and connection.get('pairingState') == 'paired'
            and 'com.apple.coredevice.feature.installapp' in capabilities)


def source_digest():
    digest = hashlib.sha256(XCODE.encode())
    files = list((ROOT / 'SignalMap').rglob('*')) + list((ROOT / 'SignalMap.xcodeproj').rglob('*'))
    for path in sorted(files):
        if not path.is_file() or 'xcuserdata' in path.parts or path.name == '.DS_Store':
            continue
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def app_digest(app):
    digest = hashlib.sha256()
    for path in sorted(app.rglob('*')):
        if path.is_file():
            digest.update(str(path.relative_to(app)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def publish(app, source):
    execute(['codesign', '--verify', '--deep', '--strict', str(app)])
    info = plistlib.loads((app / 'Info.plist').read_bytes())
    if info.get('CFBundleIdentifier') != BUNDLE or 'iPhoneOS' not in info.get('CFBundleSupportedPlatforms', []):
        raise RuntimeError('Only the physical-iPhone Signal Map app may be published.')
    profile = plistlib.loads(subprocess.check_output(['security', 'cms', '-D', '-i', str(app / 'embedded.mobileprovision')]))
    if profile['ExpirationDate'].replace(tzinfo=datetime.timezone.utc) <= datetime.datetime.now(datetime.timezone.utc):
        raise RuntimeError('Development provisioning profile expired; rebuild with signing updates.')
    fingerprint = app_digest(app)
    artifact = STATE / 'artifacts' / fingerprint / 'SignalMap.app'
    if not artifact.exists():
        artifact.parent.mkdir(parents=True, exist_ok=True)
        staging = artifact.with_name('SignalMap.app.partial')
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(app, staging, symlinks=True)
        os.replace(staging, artifact)
    manifest = dict(app=str(artifact), fingerprint=fingerprint, source=source,
                    build=str(info['CFBundleVersion']), devices=profile.get('ProvisionedDevices', []),
                    published=time.time())
    atomic_json(STATE / 'latest.json', manifest)
    say(f'Published build {manifest["build"]} ({fingerprint[:8]}). Watcher will deploy it.')
    return manifest


def build(*, force=False, register=None):
    with lock('build.lock'):
        source = source_digest()
        previous = read_json(STATE / 'latest.json')
        if not force and not register and previous and previous['source'] == source and Path(previous['app']).exists():
            say('Source unchanged; reusing the signed build.')
            return previous
        products = STATE / 'products'
        common = ['-project', 'SignalMap.xcodeproj', '-configuration', 'Debug',
                  '-allowProvisioningUpdates', f'CONFIGURATION_BUILD_DIR={products}',
                  f'OBJROOT={STATE / "objects"}', f'SYMROOT={STATE / "symbols"}']
        if register:
            say(f'Registering new development device {register}; first-time setup may take longer.')
            registration = ['xcodebuild', *common, '-scheme', 'SignalMap', '-destination', f'id={register}',
                            '-allowProvisioningDeviceRegistration', 'build']
            try:
                execute(registration, timeout=240, logfile=STATE / 'registration.log')
            except RuntimeError:
                # This Mac has an SDK-only 26.3 install; its scheme driver may demand the optional platform.
                error = (STATE / 'registration.log').read_text()
                fallback = Path(os.environ.get('SIGNALMAP_PROVISIONING_XCODE', '/Applications/Xcode.app/Contents/Developer'))
                if 'not installed' not in error or not fallback.exists() or str(fallback) == XCODE:
                    raise
                say('SDK-only Xcode cannot register through its scheme; using the installed provisioning fallback.')
                execute(registration, env=dict(ENV, DEVELOPER_DIR=str(fallback)), timeout=240,
                        logfile=STATE / 'registration-fallback.log')
        version = str(int(time.time()))
        say('Building and signing Signal Map…')
        execute(['xcodebuild', *common, '-target', 'SignalMap', '-sdk', 'iphoneos',
                 f'CURRENT_PROJECT_VERSION={version}', 'build'], timeout=300, logfile=STATE / 'build.log')
        if source_digest() != source:
            raise RuntimeError('Source changed during the build; not publishing. Run ship again.')
        return publish(products / 'SignalMap.app', source)


def deploy(device, manifest, room, installed):
    identifier = device['identifier']
    name = device.get('deviceProperties', {}).get('name', identifier)
    receipt = STATE / 'receipts' / f'{identifier}.json'
    mark = read_json(receipt, {})
    started = time.monotonic()
    if not installed:
        say(f'{name}: installing build {manifest["build"]}…')
        device_command(['device', 'install', 'app', '--device', identifier, manifest['app']], timeout=60)
        mark = dict(fingerprint=manifest['fingerprint'], build=manifest['build'], installed=time.time(), launched=False)
        atomic_json(receipt, mark)  # A launch failure must never cause repeated installation.
        say(f'{name}: installed in {time.monotonic()-started:.1f}s.')
    args = ['device', 'process', 'launch', '--device', identifier, '--terminate-existing', BUNDLE]
    if room:
        args += ['--room', room]
    # devicectl options must precede the application arguments.
    with tempfile.TemporaryDirectory(prefix='signalmap-launch-') as folder:
        output = Path(folder) / 'result.json'
        execute(['xcrun', 'devicectl', *args[:6], '--timeout', '20', '--json-output', str(output), *args[6:]], timeout=25)
        if read_json(output, {}).get('info', {}).get('outcome') != 'success':
            raise RuntimeError('Launch did not report success.')
    mark['launched'] = True
    atomic_json(receipt, mark)
    say(f'{name}: app running. Total {time.monotonic()-started:.1f}s.')


def watch(args):
    with lock('watch.lock', nonblocking=True) as lease:
        lease.seek(0); lease.truncate(); lease.write(str(os.getpid())); lease.flush()
        say('Auto-deploy running. Plug in any paired, unlocked iPhone. Ctrl-C stops it.')
        jobs, retry, seen = {}, {}, set()
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            while True:
                try:
                    devices = device_command(['list', 'devices'], timeout=10).get('devices', [])
                    latest = read_json(STATE / 'latest.json')
                    for key, job in list(jobs.items()):
                        if job.done():
                            try:
                                job.result()
                                retry.pop(key, None)
                            except Exception as error:
                                say(f'{key}: {error}\nWill retry in 15s. Unlock/Trust/Developer Mode may need attention.')
                                retry[key] = time.monotonic() + 15
                            del jobs[key]
                    connected = {d['identifier'] for d in devices if eligible(d)}
                    for identifier in seen - connected:
                        retry.pop(identifier, None)
                    seen = connected
                    if latest:
                        for device in devices:
                            identifier = device['identifier']
                            if not eligible(device) or identifier in jobs or time.monotonic() < retry.get(identifier, 0):
                                continue
                            if device.get('hardwareProperties', {}).get('udid') not in latest['devices']:
                                if 'provision' not in jobs and time.monotonic() >= retry.get('provision', 0):
                                    jobs['provision'] = pool.submit(build, register=device['hardwareProperties']['udid'])
                                continue
                            mark = read_json(STATE / 'receipts' / f'{identifier}.json', {})
                            installed = mark.get('fingerprint') == latest['fingerprint']
                            if installed and mark.get('launched'):
                                continue
                            jobs[identifier] = pool.submit(deploy, device, latest, args.room, installed)
                    elif not jobs:
                        say('No published build. Run ./scripts/dev ship in another terminal.')
                    if args.once and not jobs:
                        return
                except (RuntimeError, subprocess.TimeoutExpired) as error:
                    say(f'Device discovery: {error}')
                    if args.once:
                        raise
                time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    ship = sub.add_parser('ship', help='Incrementally build and publish to the running watcher')
    ship.add_argument('--force', action='store_true', help='Rebuild even when source is unchanged')
    watcher = sub.add_parser('watch', help='Install latest build on reachable phones as they appear')
    watcher.add_argument('--room', help='Automatically open a test room after each update')
    watcher.add_argument('--once', action='store_true', help='Deploy currently reachable devices, then exit')
    adopt = sub.add_parser('adopt', help='Publish an existing signed physical-device .app')
    adopt.add_argument('app', type=Path)
    sub.add_parser('status', help='Show published build and per-device deployment receipts')
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    if args.command == 'ship':
        build(force=args.force)
    elif args.command == 'watch':
        watch(args)
    elif args.command == 'adopt':
        with lock('build.lock'):
            publish(args.app.resolve(), source='adopted')
    elif args.command == 'status':
        latest = read_json(STATE / 'latest.json', {})
        say(f'Published build: {latest.get("build", "none")}')
        for path in sorted((STATE / 'receipts').glob('*.json')):
            say(f'{path.stem}: {read_json(path)}')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        say('Auto-deploy stopped.')
    except BlockingIOError:
        sys.exit('Another watcher is already running for this project.')
    except Exception as error:
        sys.exit(str(error))
