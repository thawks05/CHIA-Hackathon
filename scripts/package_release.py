"""Create private source/result ZIPs from explicitly selected project paths."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("cluster", "configs", "docs", "hardware", "model", "paper", "pivot",
               "rtl", "scripts", "synthesis", "tb", "tests", "verification", "workflows")
SKIP = {".git", ".tools", "target", "generated", "__pycache__", ".venv", "private", "artifacts"}
EXTENSIONS = {".py", ".v", ".sv", ".scala", ".sbt", ".properties", ".ps1", ".sh",
              ".json", ".yaml", ".yml", ".md", ".tex", ".example", ".txt"}


def source_files():
    for name in ("README.md", ".gitignore", ".gitattributes", "requirements-chia.txt"):
        yield ROOT / name
    for directory in SOURCE_DIRS:
        for current, directories, files in os.walk(ROOT / directory):
            directories[:] = sorted(d for d in directories if d not in SKIP
                                    and not (Path(current) / d).is_symlink())
            for name in sorted(files):
                path = Path(current) / name
                if not path.is_symlink() and (path.suffix in EXTENSIONS or name == ".gitignore"):
                    yield path


def archive(destination, paths, kind):
    entries = {}
    with ZipFile(destination, "x", ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(set(paths)):
            if not path.is_file() or path.is_symlink():
                continue
            name = "CHIA-Hackathon/" + path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            entries[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            bundle.writestr(name, data)
        bundle.writestr(f"CHIA-Hackathon/{kind.upper()}_MANIFEST.json", json.dumps({
            "kind": kind, "files": entries, "no_credentials_requested": True,
            "evidence": "Model data are estimates; see docs/LOCAL_RESULTS.md."}, indent=2))
    with ZipFile(destination) as bundle:
        if bundle.testzip() is not None:
            raise RuntimeError("ZIP integrity check failed")
        for name, expected in entries.items():
            if hashlib.sha256(bundle.read(name)).hexdigest() != expected["sha256"]:
                raise RuntimeError("Archive content hash mismatch: " + name)
    return {"path": str(destination), "files": len(entries), "bytes": destination.stat().st_size,
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(ROOT.parent))
    parser.add_argument("--result", action="append", default=[], help="Directory name directly under artifacts/")
    args = parser.parse_args()
    destination = Path(args.output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    result_files = []
    for name in args.result:
        if Path(name).name != name or name in (".", ".."):
            parser.error("Each --result must name one directory directly under artifacts/")
        directory = ROOT / "artifacts" / name
        if directory.is_symlink() or not directory.is_dir():
            parser.error("Missing result directory or symlink: " + name)
        result_files.extend(p for p in directory.rglob("*")
                            if p.is_file() and not p.is_symlink()
                            and directory.resolve() in p.resolve().parents)
    outputs = [archive(destination / "Pivot-ready-to-run.zip", source_files(), "source")]
    if result_files:
        outputs.append(archive(destination / "Pivot-results.zip", result_files, "results"))
    print(json.dumps(outputs, indent=2))


if __name__ == "__main__":
    main()
