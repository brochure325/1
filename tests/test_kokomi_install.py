"""Installer file behavior only; these do not simulate a live systemd install."""
import tempfile
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.install_kokomi_linux import Changes, InstallError, bridge_token, extract_release, file_image_config


class InstallerFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def archive(self, extra=None):
        archive = self.root / 'original.zip'
        with zipfile.ZipFile(archive, 'w') as package:
            package.writestr('Kokomi_bot_plugin/command_select.py', '# original dispatcher')
            package.writestr('Kokomi_bot_plugin/config.py', 'RETURN_PIC_TYPE = "base64"')
            for name, content in (extra or {}).items():
                # ZipInfo's constructor normalizes backslashes on Windows;
                # retain the deliberately malformed archive entry for this test.
                info = zipfile.ZipInfo(name)
                info.filename = name
                package.writestr(info, content)
        return archive

    def test_config_changes_only_image_type_and_preserves_credentials(self):
        before = ('class Plugin_Config:\n'
                  '    API_PASSWORD = "keep-this-local-value"\n'
                  '    RETURN_PIC_TYPE = "base64" # 保留注释\n'
                  '    BOT_PLATFORM = "qq_bot"\n')
        expected = before.replace('"base64"', "'file'")
        self.assertEqual(file_image_config(before), expected)
        self.assertEqual(file_image_config(expected), expected)

    def test_ambiguous_config_is_rejected(self):
        for source in ['OTHER = 1', 'RETURN_PIC_TYPE="file"\nRETURN_PIC_TYPE="base64"']:
            with self.assertRaises(InstallError):
                file_image_config(source)

    def test_zip_extract_and_reuse_preserve_existing_engine_config(self):
        archive = self.archive()
        target = self.root / 'release'
        engine = extract_release(archive, target)
        config = engine / 'config.py'
        config.write_text('local custom configuration')
        self.assertEqual(extract_release(archive, target), engine)
        self.assertEqual(config.read_text(), 'local custom configuration')

    def test_zip_escape_and_backslash_rejected_before_extraction(self):
        for bad in ['../outside.txt', '/outside.txt', 'Kokomi_bot_plugin/../../outside.txt', 'Kokomi_bot_plugin\\outside']:
            with self.assertRaises(InstallError):
                extract_release(self.archive({bad: 'bad'}), self.root / 'release')
            self.assertFalse((self.root / 'release').exists())

    def test_invalid_v5_zip_does_not_create_release(self):
        archive = self.root / 'v5.zip'
        with zipfile.ZipFile(archive, 'w') as package:
            package.writestr('app/main.py', '# mock')
        with self.assertRaises(InstallError):
            extract_release(archive, self.root / 'release')
        self.assertFalse((self.root / 'release').exists())

    def test_partial_existing_release_is_preserved(self):
        target = self.root / 'release'
        target.mkdir()
        marker = target / 'user-file'
        marker.write_text('keep')
        with self.assertRaises(InstallError):
            extract_release(self.archive(), target)
        self.assertEqual(marker.read_text(), 'keep')

    def test_existing_token_is_reused_and_unrecognized_env_is_not_overwritten(self):
        path = self.root / 'bridge.env'
        token = bridge_token(path)
        path.write_text('WOWS_KOKOMI_BRIDGE_TOKEN=' + token + '\n')
        self.assertEqual(bridge_token(path), token)
        path.write_text('OTHER_SETTING=keep\n')
        with self.assertRaises(InstallError):
            bridge_token(path)
        self.assertEqual(path.read_text(), 'OTHER_SETTING=keep\n')

    def test_restore_returns_original_and_removes_only_new_files(self):
        existing, new = self.root / 'old.json', self.root / 'new.conf'
        existing.write_bytes(b'{"user_setting":true}')
        changes = Changes(self.root / 'backups')
        changes.write(existing, b'first edit')
        changes.write(existing, b'second edit')
        changes.write(new, b'new setting')
        changes.restore()
        self.assertEqual(existing.read_bytes(), b'{"user_setting":true}')
        self.assertFalse(new.exists())


if __name__ == '__main__':
    unittest.main()
