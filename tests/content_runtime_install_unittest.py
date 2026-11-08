"""Installer isolation and cancellation; no package downloads in these tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from content import runtime_install as ri
from content.mcp_helper import HelperError


@unittest.skipUnless(os.name=='posix','installer qualification currently POSIX only')
class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)/'config'
        self.req=Path(self.tmp.name)/'pins.txt';self.req.write_text('mcp==2.3.0\nhttpx2==2.16.0\n')
        self.cancel=threading.Event();self.calls=[];self.states=[]
    def fake_run(self,args,cancel,timeout):
        self.calls.append([str(a) for a in args])
        if 'venv' in args:
            python=ri.target(self.root)/'bin/python';python.parent.mkdir();python.write_text('test')
    def install(self,**kwargs):
        return ri.install(self.root,self.cancel,self.states.append,_base=sys.executable,_requirements=self.req,
                          _run=kwargs.get('run',self.fake_run),_health=kwargs.get('health',lambda *_a,**_kw:None))

    def test_installs_only_owned_target_with_snapshot_and_exact_pins(self):
        self.assertEqual(self.install()['state'],'ready')
        root=ri.target(self.root);marker=json.loads((root/ri.MARKER).read_text())
        self.assertEqual(marker['state'],'ready');self.assertEqual((root/'requirements.lock').read_bytes(),self.req.read_bytes())
        pip=next(args for args in self.calls if 'install' in args)
        self.assertEqual(pip[0],str(root/'bin/python'));self.assertIn('https://pypi.org/simple',pip);self.assertIn('--only-binary=:all:',pip)
        self.assertEqual(self.states,['preparing','installing','checking','ready'])
        self.calls=[];self.assertEqual(self.install()['state'],'ready')
        self.assertFalse(any('install' in args for args in self.calls))

    def test_foreign_directory_is_not_modified(self):
        root=ri.target(self.root);root.mkdir(parents=True);keep=root/'user-file';keep.write_text('untouched')
        with self.assertRaisesRegex(HelperError,'foreign_runtime'):
            self.install(health=lambda *_a,**_kw:(_ for _ in ()).throw(HelperError('runtime_unavailable')))
        self.assertEqual(keep.read_text(),'untouched');self.assertFalse((root/ri.MARKER).exists())

    def test_failure_leaves_owned_resumable_marker(self):
        def fail(args,cancel,timeout):
            self.fake_run(args,cancel,timeout)
            if 'install' in args:raise HelperError('installation_failed')
        with self.assertRaises(HelperError):self.install(run=fail)
        self.assertEqual(json.loads((ri.target(self.root)/ri.MARKER).read_text())['state'],'incomplete')
        self.cancel.set()
        with self.assertRaisesRegex(HelperError,'cancelled'):self.install()

    def test_file_lock_rejects_concurrent_install(self):
        with ri.lock_file(ri.target(self.root).parent/'install.lock'):
            with self.assertRaisesRegex(HelperError,'installation_busy'):self.install()

    def test_cancellation_terminates_own_process_group(self):
        marker=Path(self.tmp.name)/'started';late=Path(self.tmp.name)/'late'
        child='import signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(1);Path('+repr(str(late))+').write_text("late")'
        script='import subprocess,sys,time;from pathlib import Path;subprocess.Popen([sys.executable,"-c",'+repr(child)+']);Path('+repr(str(marker))+').write_text("started");time.sleep(10)'
        def cancel():
            deadline=time.monotonic()+2
            while not marker.exists() and time.monotonic()<deadline:time.sleep(.01)
            self.cancel.set()
        thread=threading.Thread(target=cancel);thread.start()
        try:
            with self.assertRaisesRegex(HelperError,'cancelled'):ri.run([sys.executable,'-I','-B','-c',script],self.cancel,5)
        finally:thread.join(2)
        time.sleep(1.1);self.assertFalse(late.exists())


if __name__=='__main__':unittest.main()
