import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('deploy', Path(__file__).resolve().parents[2] / 'scripts/dev_deploy.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)

class DeployTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.state = patch.object(d, 'STATE', Path(self.folder.name))
        self.state.start(); self.addCleanup(self.state.stop)
        self.device = dict(identifier='phone', hardwareProperties=dict(deviceType='iPhone', reality='physical'),
                           connectionProperties=dict(pairingState='paired'),
                           capabilities=[dict(featureIdentifier='com.apple.coredevice.feature.installapp')])
        self.manifest = dict(fingerprint='hash', build='5', app='/tmp/SignalMap.app')

    def test_only_reachable_physical_iphones(self):
        self.assertTrue(d.eligible(self.device))
        self.device['capabilities'] = []
        self.assertFalse(d.eligible(self.device))
        self.device['hardwareProperties']['reality'] = 'simulated'
        self.assertFalse(d.eligible(self.device))

    def test_failed_install_never_records_success(self):
        with patch.object(d, 'device_command', side_effect=RuntimeError('locked')):
            with self.assertRaises(RuntimeError):
                d.deploy(self.device, self.manifest, None, False)
        self.assertFalse((d.STATE / 'receipts/phone.json').exists())

    def test_launch_failure_does_not_force_reinstall(self):
        with patch.object(d, 'device_command') as install, patch.object(d, 'execute', side_effect=RuntimeError('untrusted')):
            with self.assertRaises(RuntimeError):
                d.deploy(self.device, self.manifest, None, False)
            receipt = d.read_json(d.STATE / 'receipts/phone.json')
            self.assertEqual(receipt['fingerprint'], 'hash')
            self.assertFalse(receipt['launched'])
            with self.assertRaises(RuntimeError):
                d.deploy(self.device, self.manifest, None, True)
            self.assertEqual(install.call_count, 1)

    def test_launch_options_precede_app_arguments(self):
        def launch(args, **kwargs):
            self.assertLess(args.index('--json-output'), args.index(d.BUNDLE))
            self.assertEqual(args[args.index('--room') + 1], 'ROOM')
            Path(args[args.index('--json-output') + 1]).write_text(json.dumps({'info': {'outcome': 'success'}}))
        with patch.object(d, 'device_command'), patch.object(d, 'execute', side_effect=launch):
            d.deploy(self.device, self.manifest, 'ROOM', False)
        self.assertTrue(d.read_json(d.STATE / 'receipts/phone.json')['launched'])

    def test_immutable_fingerprint_includes_contents(self):
        app = d.STATE / 'app'; app.mkdir()
        binary = app / 'binary'; binary.write_bytes(b'one')
        first = d.app_digest(app)
        binary.write_bytes(b'two')
        self.assertNotEqual(first, d.app_digest(app))

    def test_atomic_manifest_updates(self):
        path = d.STATE / 'latest.json'
        d.atomic_json(path, {'build': 1}); d.atomic_json(path, {'build': 2})
        self.assertEqual(d.read_json(path), {'build': 2})

if __name__ == '__main__':
    unittest.main()
