"""Native process handles must distinguish a live child from its exited PID."""
import subprocess
import sys
import unittest

from cold_agent_benchmark import alive


@unittest.skipUnless(sys.platform == 'win32', 'Win64 benchmark runner')
class ColdLivenessTest(unittest.TestCase):
    def test_live_and_exited_owned_process(self):
        with subprocess.Popen([sys.executable, '-c', 'input()'],
                              stdin=subprocess.PIPE, text=True) as child:
            self.assertTrue(alive(child.pid))
            child.communicate('\n', timeout=10)
            self.assertEqual(child.returncode, 0)
            self.assertFalse(alive(child.pid))
