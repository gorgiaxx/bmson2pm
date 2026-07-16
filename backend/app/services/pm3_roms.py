from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .pm3_workspace import Pm3Workspace, Pm3WorkspaceError


SQUASHFS_MAGIC = b"hsqs"
PM3_ROM_TIMEOUT_SECONDS = 15 * 60


class Pm3RomBuildError(ValueError):
    pass


@dataclass(frozen=True)
class Pm3RomSong:
    song_id: int
    mv_id: int
    background: bytes
    preview: bytes


def pm3_sound_bundle(song_id: int) -> int:
    if song_id <= 39:
        return 1
    if song_id <= 69:
        return 2
    if song_id <= 99:
        return 3
    if song_id <= 159:
        return 4
    if song_id <= 199:
        return 6
    if song_id <= 210:
        return 5
    return 7


def patch_stage_mv(payload: bytes, song_id: int, mv_id: int) -> bytes:
    marker = re.search(
        rb"(?m)^[ \t]*MV[ \t]*=[ \t]*\r?\n[ \t]*\{[ \t]*\r?$",
        payload,
    )
    if marker is None:
        raise Pm3RomBuildError("stage.lua 中找不到 StageConfig.MV 表")
    closing = re.search(rb"(?m)^[ \t]*\},[ \t]*\r?$", payload[marker.end():])
    if closing is None:
        raise Pm3RomBuildError("stage.lua 中的 StageConfig.MV 表没有结束标记")
    table_end = marker.end() + closing.start()
    table = payload[marker.end():table_end]
    entry = re.compile(
        rb"(\[[ \t]*" + str(song_id).encode("ascii") + rb"[ \t]*\][ \t]*=[ \t]*)([0-9]+)"
    )
    matches = list(entry.finditer(table))
    if len(matches) > 1:
        raise Pm3RomBuildError(f"stage.lua 中曲目 {song_id} 的 MV 映射重复")
    if matches:
        match = matches[0]
        replacement = match.group(1) + str(mv_id).encode("ascii")
        patched_table = table[:match.start()] + replacement + table[match.end():]
        result = payload[:marker.end()] + patched_table + payload[table_end:]
    else:
        newline = b"\r\n" if b"\r\n" in payload[:marker.end()] else b"\n"
        addition = f"\t\t[{song_id}] = {mv_id},".encode("ascii") + newline
        result = payload[:table_end] + addition + payload[table_end:]

    patched_marker = re.search(
        rb"(?m)^[ \t]*MV[ \t]*=[ \t]*\r?\n[ \t]*\{[ \t]*\r?$",
        result,
    )
    patched_closing = re.search(
        rb"(?m)^[ \t]*\},[ \t]*\r?$", result[patched_marker.end():]
    ) if patched_marker else None
    if patched_marker is None or patched_closing is None:
        raise Pm3RomBuildError("stage.lua MV 映射写入后结构验证失败")
    patched_table = result[
        patched_marker.end():patched_marker.end() + patched_closing.start()
    ]
    verified = list(entry.finditer(patched_table))
    if len(verified) != 1 or int(verified[0].group(2)) != mv_id:
        raise Pm3RomBuildError("stage.lua MV 映射写入后验证失败")
    return result


class Pm3RomBuilder:
    def __init__(self, workspace: Pm3Workspace) -> None:
        self.workspace = workspace

    def inspect(self, song_id: int) -> dict[str, Any]:
        state = self.inspect_many([song_id])
        return {**state, "bundle": pm3_sound_bundle(song_id)}

    def inspect_many(self, song_ids: list[int]) -> dict[str, Any]:
        if not song_ids:
            raise Pm3RomBuildError("ROM 构建至少需要一首歌曲")
        if any(song_id < 0 or song_id > 999 for song_id in song_ids):
            raise Pm3RomBuildError("PM3 曲目序号必须在 0..999")
        if len(set(song_ids)) != len(song_ids):
            raise Pm3RomBuildError("ROM 构建曲目序号不能重复")
        bundles = sorted({pm3_sound_bundle(song_id) for song_id in song_ids})
        mksquashfs = self._tool("BMSON2PM_MKSQUASHFS", "mksquashfs")
        unsquashfs = self._tool("BMSON2PM_UNSQUASHFS", "unsquashfs")
        missing: list[str] = []
        paths: dict[str, Path] = {}
        for key, relative, expect in (
            ("sound_rom", "ROMS/sound.rom", "file"),
            ("lua_source", "media/lua_script", "directory"),
        ):
            try:
                paths[key] = self.workspace.resolve("game", relative, expect=expect)
            except Pm3WorkspaceError:
                missing.append(relative)
        for bundle in bundles:
            if bundle > 6:
                continue
            for key, relative in (
                (f"background_source_{bundle}", f"media/SOUND_BG{bundle}"),
                (f"preview_source_{bundle}", f"media/SOUND_PRE{bundle}"),
            ):
                try:
                    paths[key] = self.workspace.resolve("game", relative, expect="directory")
                except Pm3WorkspaceError:
                    missing.append(relative)
        stage = paths.get("lua_source", Path()) / "stage.lua"
        if "lua_source" in paths and not stage.is_file():
            missing.append("media/lua_script/stage.lua")
        sound_rom = paths.get("sound_rom")
        if sound_rom is not None:
            try:
                with sound_rom.open("rb") as stream:
                    magic = stream.read(4)
                if magic != SQUASHFS_MAGIC:
                    missing.append("ROMS/sound.rom（不是 SquashFS 4 镜像）")
            except OSError:
                missing.append("ROMS/sound.rom（无法读取）")
        if mksquashfs is None:
            missing.append("mksquashfs")
        if unsquashfs is None:
            missing.append("unsquashfs")
        files = ["ROMS/lua_script.rom", "ROMS/sound.rom"]
        for bundle in bundles:
            files.extend((f"ROMS/SOUND_BG{bundle}.rom", f"ROMS/SOUND_PRE{bundle}.rom"))
        return {
            "available": not missing,
            "song_ids": song_ids,
            "bundles": bundles,
            "files": files,
            "missing": list(dict.fromkeys(missing)),
            "tools": {
                "mksquashfs": mksquashfs.name if mksquashfs else None,
                "unsquashfs": unsquashfs.name if unsquashfs else None,
            },
            "source": "game:ROMS + game:media（只读）",
        }

    def build(
        self,
        *,
        song_id: int,
        mv_id: int,
        background: bytes,
        preview: bytes,
    ) -> dict[str, bytes]:
        return self.build_many([
            Pm3RomSong(
                song_id=song_id,
                mv_id=mv_id,
                background=background,
                preview=preview,
            )
        ])

    def build_many(self, songs: list[Pm3RomSong]) -> dict[str, bytes]:
        state = self.inspect_many([song.song_id for song in songs])
        if not state["available"]:
            raise Pm3RomBuildError(
                f"PM3 ROM 构建环境不完整：{'、'.join(state['missing'])}"
            )
        bundles = [int(bundle) for bundle in state["bundles"]]
        mksquashfs_path = self._tool("BMSON2PM_MKSQUASHFS", "mksquashfs")
        unsquashfs_path = self._tool("BMSON2PM_UNSQUASHFS", "unsquashfs")
        if mksquashfs_path is None or unsquashfs_path is None:
            raise Pm3RomBuildError("PM3 ROM 构建工具在预检后变得不可用")
        mksquashfs = str(mksquashfs_path)
        unsquashfs = str(unsquashfs_path)
        lua_source = self.workspace.resolve("game", "media/lua_script", expect="directory")
        sound_rom = self.workspace.resolve("game", "ROMS/sound.rom", expect="file")

        with tempfile.TemporaryDirectory(prefix="bmson2pm-pm3-rom-") as directory:
            temporary = Path(directory)
            lua_root = temporary / "lua_script"
            sound_root = temporary / "sound"
            output = temporary / "output"
            output.mkdir()

            shutil.copytree(lua_source, lua_root, symlinks=True)
            stage_path = lua_root / "stage.lua"
            stage_payload = stage_path.read_bytes()
            for song in songs:
                stage_payload = patch_stage_mv(stage_payload, song.song_id, song.mv_id)
            stage_path.write_bytes(stage_payload)

            background_roots: dict[int, Path] = {}
            preview_roots: dict[int, Path] = {}
            for bundle in bundles:
                background_root = temporary / f"SOUND_BG{bundle}"
                preview_root = temporary / f"SOUND_PRE{bundle}"
                if bundle <= 6:
                    shutil.copytree(
                        self.workspace.resolve(
                            "game", f"media/SOUND_BG{bundle}", expect="directory"
                        ),
                        background_root,
                        symlinks=True,
                    )
                    shutil.copytree(
                        self.workspace.resolve(
                            "game", f"media/SOUND_PRE{bundle}", expect="directory"
                        ),
                        preview_root,
                        symlinks=True,
                    )
                else:
                    background_root.mkdir()
                    preview_root.mkdir()
                background_roots[bundle] = background_root
                preview_roots[bundle] = preview_root

            for song in songs:
                bundle = pm3_sound_bundle(song.song_id)
                (background_roots[bundle] / f"BG_{song.song_id:03d}.ogg").write_bytes(
                    song.background
                )
                (preview_roots[bundle] / f"p{song.song_id:03d}.wav").write_bytes(
                    song.preview
                )

            self._run(
                [unsquashfs, "-no-progress", "-d", str(sound_root), str(sound_rom)],
                "展开 sound.rom",
            )
            for song in songs:
                bundle = pm3_sound_bundle(song.song_id)
                background_name = f"BG_{song.song_id:03d}.ogg"
                preview_name = f"p{song.song_id:03d}.wav"
                self._replace_symlink(
                    sound_root / "BG" / background_name,
                    f"../../SOUND_BG{bundle}/./{background_name}",
                )
                self._replace_symlink(
                    sound_root / "preview" / preview_name,
                    f"../../SOUND_PRE{bundle}/./{preview_name}",
                )

            builds = [
                (lua_root, output / "lua_script.rom"),
                (sound_root, output / "sound.rom"),
            ]
            for bundle in bundles:
                builds.extend((
                    (background_roots[bundle], output / f"SOUND_BG{bundle}.rom"),
                    (preview_roots[bundle], output / f"SOUND_PRE{bundle}.rom"),
                ))
            for source, destination in builds:
                self._build_image(mksquashfs, source, destination)
                self._verify_image(unsquashfs, destination)

            for song in songs:
                self._verify_contents(
                    unsquashfs,
                    output=output,
                    song_id=song.song_id,
                    mv_id=song.mv_id,
                    bundle=pm3_sound_bundle(song.song_id),
                    background=song.background,
                    preview=song.preview,
                )
            return {
                f"ROMS/{path.name}": path.read_bytes()
                for _, path in builds
            }

    @staticmethod
    def _tool(environment: str, fallback: str) -> Path | None:
        configured = os.getenv(environment)
        candidate = configured or shutil.which(fallback)
        if not candidate:
            return None
        path = Path(candidate).expanduser()
        return path.resolve() if path.is_file() and os.access(path, os.X_OK) else None

    @staticmethod
    def _replace_symlink(path: Path, target: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            if path.is_dir() and not path.is_symlink():
                raise Pm3RomBuildError(f"无法用链接替换目录：{path.name}")
            path.unlink()
        path.symlink_to(target)

    def _build_image(self, tool: str, source: Path, destination: Path) -> None:
        self._run(
            [
                tool,
                str(source),
                str(destination),
                "-noappend",
                "-comp", "gzip",
                "-b", "131072",
                "-all-root",
                "-no-xattrs",
                "-no-progress",
            ],
            f"构建 {destination.name}",
        )

    def _verify_image(self, tool: str, image: Path) -> None:
        if not image.is_file():
            raise Pm3RomBuildError(f"{image.name} 没有有效的 SquashFS magic")
        with image.open("rb") as stream:
            magic = stream.read(4)
        if magic != SQUASHFS_MAGIC:
            raise Pm3RomBuildError(f"{image.name} 没有有效的 SquashFS magic")
        self._run([tool, "-s", str(image)], f"验证 {image.name}")

    def _verify_contents(
        self,
        tool: str,
        *,
        output: Path,
        song_id: int,
        mv_id: int,
        bundle: int,
        background: bytes,
        preview: bytes,
    ) -> None:
        stage = self._run_bytes(
            [tool, "-cat", str(output / "lua_script.rom"), "stage.lua"],
            "回读 stage.lua",
        )
        expected = re.compile(
            rb"\[[ \t]*" + str(song_id).encode("ascii")
            + rb"[ \t]*\][ \t]*=[ \t]*" + str(mv_id).encode("ascii") + rb"\b"
        )
        if expected.search(stage) is None:
            raise Pm3RomBuildError("lua_script.rom 回读时找不到目标 MV 映射")
        actual_background = self._run_bytes(
            [
                tool, "-cat", str(output / f"SOUND_BG{bundle}.rom"),
                f"BG_{song_id:03d}.ogg",
            ],
            "回读主音乐",
        )
        actual_preview = self._run_bytes(
            [
                tool, "-cat", str(output / f"SOUND_PRE{bundle}.rom"),
                f"p{song_id:03d}.wav",
            ],
            "回读试听",
        )
        if actual_background != background or actual_preview != preview:
            raise Pm3RomBuildError("音频 ROM 回读内容与输入不一致")
        listing = self._run_bytes(
            [tool, "-ll", str(output / "sound.rom")],
            "回读 sound.rom 链接",
        ).decode("utf-8", errors="replace")
        for expected_link in (
            f"BG/BG_{song_id:03d}.ogg -> ../../SOUND_BG{bundle}/./BG_{song_id:03d}.ogg",
            f"preview/p{song_id:03d}.wav -> ../../SOUND_PRE{bundle}/./p{song_id:03d}.wav",
        ):
            if expected_link not in listing:
                raise Pm3RomBuildError(f"sound.rom 缺少链接：{expected_link}")

    def _run(self, command: list[str], action: str) -> None:
        self._run_bytes(command, action)

    @staticmethod
    def _run_bytes(command: list[str], action: str) -> bytes:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=PM3_ROM_TIMEOUT_SECONDS,
                env={**os.environ, "LC_ALL": "C", "LANG": "C"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Pm3RomBuildError(f"{action}失败：{exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
            raise Pm3RomBuildError(
                f"{action}失败：{detail[-1] if detail else f'退出码 {result.returncode}'}"
            )
        return result.stdout
