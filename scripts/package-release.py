"""Build a deterministic manual-install archive from tracked integration files."""

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INTEGRATION = "custom_components/public_transport_victoria"


def package(output, tag):
    manifest = json.loads((ROOT / INTEGRATION / "manifest.json").read_text())
    version = manifest["version"]
    if tag != version:
        raise ValueError("Release tag must exactly match the integration version")
    paths = (
        subprocess.check_output(["git", "ls-files", "-z", INTEGRATION], cwd=ROOT)
        .decode()
        .split("\0")
    )
    paths = sorted(path for path in paths if path)
    assert paths and f"{INTEGRATION}/__init__.py" in paths
    for path in paths:
        if Path(path).name != "LICENSE" and Path(path).suffix not in {
            ".py",
            ".json",
            ".png",
            ".svg",
        }:
            raise ValueError(f"Unexpected integration artifact: {path}")
    paths += ["LICENSE", "LICENSE_SCOPE.md"]
    paths.sort()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            entry = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, (ROOT / path).read_bytes())
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == paths
        installed = json.loads(archive.read(f"{INTEGRATION}/manifest.json"))
        assert installed["version"] == tag and installed["domain"] == manifest["domain"]
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{checksum}  {output.name}\n"
    )
    print(json.dumps({"version": version, "files": len(paths), "sha256": checksum}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    arguments = parser.parse_args()
    package(arguments.output, arguments.tag)
