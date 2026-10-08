"""Read a selected local management revision and serve its hash-matched original files."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

QUERY = """
import os,json
from sqlalchemy import create_engine,text
if os.environ.get('JUYA_ENVIRONMENT') != 'local':
    raise SystemExit('Only the local management database may be previewed')
engine=create_engine(os.environ['JUYA_DATABASE_URL'])
with engine.connect() as connection:
    row=connection.execute(text('''
      SELECT s.public_id scene_id,r.public_id revision_id,r.edit_version content_version,
             r.content_snapshot content
      FROM scene_revision r JOIN scene s ON s.id=r.scene_id
      WHERE r.public_id=:revision AND r.status IN ('DRAFT','PUBLISHED')
    '''),{'revision':os.environ['PREVIEW_REVISION_ID']}).mappings().one()
    scene=dict(row)
    if isinstance(scene['content'],str): scene['content']=json.loads(scene['content'])
    media=[dict(row) for row in connection.execute(text('''
      SELECT public_id,sha256,content_type,status FROM media_asset
    ''')).mappings()]
    bindings=[dict(row) for row in connection.execute(text('''
      SELECT t.public_id target_id,v.public_id version_id,m.public_id asset_id
      FROM audio_version v JOIN audio_target t ON t.id=v.target_id
      JOIN media_asset m ON m.id=v.asset_id WHERE v.status='ACTIVE'
    ''')).mappings()]
print(json.dumps({'scene':scene,'media':media,'bindings':bindings},ensure_ascii=False))
engine.dispose()
"""


def export_manifest(revision_id: str, media_directory: Path, container: str) -> dict[str, Any]:
    # 功能:从管理端导出指定发布版本的真实内容与媒体清单
    # 参数:
    #     revision_id: 需要访问或固定的场景发布修订标识
    #     media_directory: 导出或加载真实音频和图片的本地目录
    #     container: 用于导出真实发布内容的管理端容器名称
    # 返回:发布场景契约与媒体文件路径、类型和哈希清单
    """Read revision_id in the local container and match referenced files under media_directory"""
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            f"PREVIEW_REVISION_ID={revision_id}",
            container,
            "python",
            "-",
        ],
        input=QUERY.encode(),
        capture_output=True,
        check=False,
    )
    if result.returncode:
        # Database errors may include connection details; never print raw container output.
        raise RuntimeError("Cannot read the selected revision from the local management database")
    data = json.loads(result.stdout.decode("utf-8"))
    content = data["scene"]["content"]
    refs: dict[str, str] = {}
    for asset_id in (content.get("original_image_asset_id"), content.get("cover_asset_id")):
        if asset_id:
            refs[asset_id] = asset_id
    audio = content.get("audio")
    if not audio:
        raise ValueError("The selected management revision has no recording")
    bindings = {(row["target_id"], row["version_id"]): row["asset_id"] for row in data["bindings"]}
    if bindings.get((audio["target_id"], audio["version_id"])) != audio["asset_id"]:
        raise ValueError("The management recording version is not active or its binding changed")
    refs[audio["asset_id"]] = audio["asset_id"]
    for entry in content.get("vocabulary", []) + content.get("chunks", []):
        if entry.get("audio_target_id"):
            target = entry["audio_target_id"]
            asset_id = bindings.get((target, entry.get("audio_version_id")))
            if not asset_id:
                raise ValueError("A referenced entry recording has no active version")
            refs[target] = asset_id
    files = {
        hashlib.sha256(path.read_bytes()).hexdigest(): path.resolve()
        for path in media_directory.iterdir()
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".wav", ".mp3"}
    }
    assets = {row["public_id"]: row for row in data["media"]}
    media = {}
    for resource_id, asset_id in refs.items():
        asset = assets[asset_id]
        path = files.get(asset["sha256"])
        if asset["status"] != "CONFIRMED" or path is None:
            raise ValueError("A confirmed management asset has no matching local source file")
        media[resource_id] = {
            "path": str(path),
            "sha256": asset["sha256"],
            "content_type": asset["content_type"],
        }
    return {"scene": data["scene"], "media": media}


def main() -> None:
    # 功能:导出真实内容并启动本地小程序API
    # 参数:
    #     无形参。
    # 返回:无返回值。
    """Export a fresh non-secret management snapshot and start a loopback-only local preview"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--media-directory", required=True, type=Path)
    parser.add_argument("--admin-container", default="juya-admin-api-admin-api-1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    manifest = export_manifest(args.revision, args.media_directory, args.admin_container)
    # Validate the contract, bound hashes and readable files before changing the running service.
    from juya_miniapp_api.api.local_real_content import LocalRealContent

    with tempfile.NamedTemporaryFile(
        mode="w", suffix="-juya-preview.json", encoding="utf-8", delete=False
    ) as output:
        json.dump(manifest, output, ensure_ascii=False)
        manifest_path = Path(output.name)
    try:
        preview = LocalRealContent(manifest_path)
        print(
            json.dumps(
                {
                    "scene_id": preview.scene_id,
                    "revision_id": preview.revision_id,
                    "title": preview.scene["content"]["title_zh"],
                    "duration_ms": preview.scene["content"]["audio"]["duration_ms"],
                    "media_count": len(preview.resources),
                    "published": False,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if args.check_only:
            return
        environment = dict(os.environ)
        environment.update(
            JUYA_ENVIRONMENT="local",
            JUYA_LOCAL_DEV_MODE="true",
            JUYA_LOCAL_CONTENT_FILE=str(manifest_path),
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "juya_miniapp_api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.port),
            ],
            env=environment,
            cwd=Path(__file__).resolve().parents[1],
            check=False,
        )
        raise SystemExit(result.returncode)
    finally:
        manifest_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
