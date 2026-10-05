"""Check cleanup failure handling without contacting a Docker engine."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("test-container.sh")


class CleanupTests(unittest.TestCase):
    def run_script(self, *, fail_list="", leftover="", check_status="0"):
        with tempfile.TemporaryDirectory(prefix="ptv-cleanup-test-") as directory:
            root = Path(directory)
            shutil.copyfile(SCRIPT, root / SCRIPT.name)
            git = root / "git"
            git.write_text("#!/bin/sh\nprintf 'testrevision\\n'\n")
            docker = root / "docker"
            docker.write_text('''#!/bin/sh
case "$1 $2" in
    "buildx create") printf '%s\\n' "$4" > "$PTV_FAKE_RESOURCE" ;;
    "rm -f"|"image rm"|"buildx rm") exit 1 ;;
    "container ls"|"image ls"|"buildx ls")
        if [ "$1" = "$PTV_FAKE_FAIL_LIST" ]; then
            echo "Cannot connect to Docker" >&2
            exit 1
        fi
        if [ "$1" = "$PTV_FAKE_LEFTOVER" ]; then
            name=$(cat "$PTV_FAKE_RESOURCE")
            case "$1" in
                image) printf 'ptv-test-harness:%s\\n' "${name#ptv-tests-}" ;;
                *) printf '%s\\n' "$name" ;;
            esac
        else
            printf 'unrelated-resource\\n'
        fi
        ;;
    "run --rm") exit "$PTV_FAKE_CHECK_STATUS" ;;
esac
''')
            for executable in (git, docker):
                executable.chmod(0o755)
            env = dict(
                os.environ,
                PATH=str(root) + os.pathsep + os.environ["PATH"],
                PTV_FAKE_RESOURCE=str(root / "resource-name"),
                PTV_FAKE_FAIL_LIST=fail_list,
                PTV_FAKE_LEFTOVER=leftover,
                PTV_FAKE_CHECK_STATUS=check_status,
            )
            return subprocess.run(
                ["sh", str(root / SCRIPT.name)], env=env,
                capture_output=True, text=True, check=False,
            )

    def assert_recovery_commands(self, result):
        for command in ("docker rm -f ptv-tests-", "docker image rm ptv-test-harness:",
                        "docker buildx rm ptv-tests-"):
            self.assertIn(command, result.stderr)

    def test_confirmed_absence_allows_failed_removal(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")

    def test_listing_errors_fail_cleanup(self):
        for resource in ("container", "image", "buildx"):
            with self.subTest(resource=resource):
                result = self.run_script(fail_list=resource)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Cleanup unverified", result.stderr)
                self.assert_recovery_commands(result)

    def test_remaining_resources_fail_cleanup(self):
        for resource in ("container", "image", "buildx"):
            with self.subTest(resource=resource):
                result = self.run_script(leftover=resource)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Cleanup incomplete", result.stderr)
                self.assert_recovery_commands(result)

    def test_check_failure_is_preserved(self):
        result = self.run_script(check_status="7")
        self.assertEqual(result.returncode, 7)


if __name__ == "__main__":
    unittest.main()
