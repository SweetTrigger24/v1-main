"""Build frontend trên PC và đóng gói bản triển khai Pi, không mang venv/node_modules."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from pi_deploy import REFLEX_VERSION, source_digest, validate_frontend

ROOT = Path(__file__).resolve().parent


def build_frontend(root=ROOT):
    root = Path(root).resolve()
    if importlib.metadata.version("reflex") != REFLEX_VERSION:
        raise ValueError(f"Cần Reflex {REFLEX_VERSION}; cài requirements.txt trước khi build")
    env = os.environ.copy()
    env.update(REFLEX_SSR="false", REFLEX_TELEMETRY_ENABLED="false", REFLEX_CHECK_LATEST_VERSION="false",
               REFLEX_API_URL="http://localhost:3000", REFLEX_FRONTEND_PORT="3000", REFLEX_BACKEND_PORT="3000")
    if "REFLEX_DIR" not in env:
        cached = root.parent / ".reflex"
        env["REFLEX_DIR"] = str(cached if cached.exists() else root / ".reflex")
    subprocess.run([sys.executable, "-m", "reflex", "export", "--frontend-only", "--no-zip", "--no-ssr"],
                   cwd=root, env=env, check=True)
    compiled = root / ".web/build/client"
    if not (compiled / "index.html").is_file():
        raise ValueError("Reflex chưa tạo index.html")
    deploy = root / "deploy"
    deploy.mkdir(exist_ok=True)
    stage = deploy / ("frontend-stage-" + uuid.uuid4().hex)
    shutil.copytree(compiled, stage)
    files = {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(stage.rglob("*")) if p.is_file()}
    source_hash = source_digest(root)
    build_id = hashlib.sha256((source_hash + json.dumps(files, sort_keys=True)).encode()).hexdigest()[:16]
    manifest = {"schema": 1, "reflex_version": REFLEX_VERSION, "api_url": "http://localhost:3000",
                "source_sha256": source_hash, "build_id": build_id, "files": files,
                "built_at_utc": datetime.now(timezone.utc).isoformat()}
    target, previous = deploy / "frontend", deploy / "frontend.previous"
    # Mọi thư mục thay thế/xóa đều nằm ngay trong deploy của workspace này.
    for path in (stage, target, previous):
        if path.resolve().parent != deploy.resolve():
            raise ValueError("Đường dẫn build nằm ngoài deploy")
    if previous.exists():
        shutil.rmtree(previous)
    if target.exists():
        target.rename(previous)
    stage.rename(target)
    (deploy / "frontend_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    validate_frontend(root)
    return manifest


def package(root=ROOT, destination=None):
    root = Path(root).resolve()
    validate_frontend(root)
    destination = Path(destination) if destination else root.parent / "iot-pi4-2gb.zip"
    destination = destination.resolve()
    excluded = {".web", ".states", ".venv", "venv", "__pycache__", ".pytest_cache", ".git", ".reflex", "artifacts", "node_modules"}
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob("*")):
                relative = path.relative_to(root)
                if (not path.is_file()
                        or any(part in excluded or part.startswith("frontend-stage-") or part == "frontend.previous" for part in relative.parts)
                        or path.name == ".env" or ".sqlite3" in path.name or path.suffix in {".pyc", ".zip", ".keras", ".tflite"}
                        or relative.parts[0] == "model" or relative.parts[:2] == ("data", "backups")
                        or path.resolve() in {destination, temporary}):
                    continue
                archive.write(path, Path(root.name) / relative)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("Gói ZIP bị lỗi checksum")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Chỉ kiểm tra frontend, không build hay dùng Node")
    parser.add_argument("--no-build", action="store_true", help="Chỉ đóng gói frontend đã build hợp lệ")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.check:
            manifest = validate_frontend(ROOT)
            print("Frontend hợp lệ:", manifest["build_id"])
            return
        if not args.no_build:
            build_frontend()
        print("Đã tạo gói triển khai:", package(destination=args.output))
    except (ValueError, FileNotFoundError, importlib.metadata.PackageNotFoundError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
