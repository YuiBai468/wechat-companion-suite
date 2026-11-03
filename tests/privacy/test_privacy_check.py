import importlib.util
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('privacy_check',ROOT/'tools/privacy_check.py')
checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)

class PrivacyCheckTests(unittest.TestCase):
    def setUp(self):
        # A source-folder fixture must live outside the checkout. Within a Git
        # checkout the scanner intentionally reads tracked files only.
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=pathlib.Path(self.temp.name)
    def test_secret_in_example_is_still_detected(self):
        (self.root/'.env.example').write_text('API_KEY='+'sk-'+'a'*32,encoding='utf-8')
        result=checker.scan(self.root)
        self.assertFalse(result['ok']);self.assertTrue(any(f['rule']=='provider_key' for f in result['findings']))
    def test_runtime_database_directory_is_not_publishable(self):
        p=self.root/'desktop/data/health.json';p.parent.mkdir(parents=True)
        p.write_text('{"synthetic": true}',encoding='utf-8')
        self.assertFalse(checker.scan(self.root)['ok'])
    def test_blank_example_is_allowed(self):
        (self.root/'.env.example').write_text('API_KEY=\n',encoding='utf-8')
        self.assertTrue(checker.scan(self.root)['ok'])
    def test_staged_content_is_scanned_even_if_worktree_is_cleaned(self):
        p=self.root/'example.txt';p.write_text('safe working copy',encoding='utf-8')
        blob=('sk-'+'b'*32).encode()
        def index(root,*args):
            if args==('ls-files','-z'):return b'example.txt\0'
            if args==('show',':example.txt'):return blob
            raise AssertionError(args)
        with patch.object(checker,'git',side_effect=index):
            self.assertFalse(checker.scan(self.root,staged=True)['ok'])
            self.assertTrue(checker.scan(self.root,staged=False)['ok'])
    def test_reports_do_not_echo_secret_values(self):
        value='sk-'+'c'*32;(self.root/'example.txt').write_text(value,encoding='utf-8')
        self.assertNotIn(value,str(checker.scan(self.root)))

if __name__=='__main__':unittest.main()
