from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..adapters.pm3_reservations import reserved_ui
from .pm3_ui import Pm3UiError, Pm3UiText, patch_pm3_ui_swf
from .pm3_workspace import Pm3Workspace, Pm3WorkspaceError


SQUASHFS_MAGIC = b"hsqs"
PM3_ROM_TIMEOUT_SECONDS = 15 * 60


class Pm3RomBuildError(ValueError):
    pass


@dataclass(frozen=True)
class Pm3RomKeySound:
    relative_path: str
    payload: bytes


@dataclass(frozen=True)
class Pm3RomMv:
    mv_id: int
    payload: bytes


@dataclass(frozen=True)
class Pm3RomSong:
    song_id: int
    mv_id: int
    title: str
    artist: str
    background: bytes
    preview: bytes
    key_sounds: tuple[Pm3RomKeySound, ...] = ()
    custom_mv: Pm3RomMv | None = None


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

    def inspect(self, song_id: int, *, custom_mv_id: int | None = None) -> dict[str, Any]:
        state = self.inspect_many(
            [song_id],
            custom_mv_ids=[custom_mv_id] if custom_mv_id is not None else [],
        )
        return {**state, "bundle": pm3_sound_bundle(song_id)}

    def inspect_many(
        self,
        song_ids: list[int],
        *,
        custom_mv_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        if not song_ids:
            raise Pm3RomBuildError("ROM 构建至少需要一首歌曲")
        if any(song_id < 0 or song_id > 999 for song_id in song_ids):
            raise Pm3RomBuildError("PM3 曲目序号必须在 0..999")
        if len(set(song_ids)) != len(song_ids):
            raise Pm3RomBuildError("ROM 构建曲目序号不能重复")
        selected_mv_ids = sorted(set(custom_mv_ids or []))
        if any(mv_id < 20 or mv_id > 99 for mv_id in selected_mv_ids):
            raise Pm3RomBuildError("自定义 PM3 MV ID 必须在 20..99")
        bundles = sorted({pm3_sound_bundle(song_id) for song_id in song_ids})
        mksquashfs = self._tool("BMSON2PM_MKSQUASHFS", "mksquashfs")
        unsquashfs = self._tool("BMSON2PM_UNSQUASHFS", "unsquashfs")
        missing: list[str] = []
        paths: dict[str, Path] = {}
        for key, relative, expect in (
            ("sound_rom", "ROMS/sound.rom", "file"),
            ("ui_rom", "ROMS/ui.rom", "file"),
            ("lua_source", "media/lua_script", "directory"),
        ):
            try:
                paths[key] = self.workspace.resolve("game", relative, expect=expect)
            except Pm3WorkspaceError:
                missing.append(relative)
        if selected_mv_ids:
            for key, relative, expect in (
                ("ui_mv6_source", "media/ui_mv6", "directory"),
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
        for key, label in (("sound_rom", "ROMS/sound.rom"), ("ui_rom", "ROMS/ui.rom")):
            rom_path = paths.get(key)
            if rom_path is None:
                continue
            try:
                with rom_path.open("rb") as stream:
                    magic = stream.read(4)
                if magic != SQUASHFS_MAGIC:
                    missing.append(f"{label}（不是 SquashFS 4 镜像）")
            except OSError:
                missing.append(f"{label}（无法读取）")
        if mksquashfs is None:
            missing.append("mksquashfs")
        if unsquashfs is None:
            missing.append("unsquashfs")
        files = ["ROMS/lua_script.rom", "ROMS/sound.rom", "ROMS/ui.rom"]
        if selected_mv_ids:
            files.append("ROMS/ui_mv6.rom")
        for bundle in bundles:
            files.extend((f"ROMS/SOUND_BG{bundle}.rom", f"ROMS/SOUND_PRE{bundle}.rom"))
        return {
            "available": not missing,
            "song_ids": song_ids,
            "bundles": bundles,
            "custom_mv_ids": selected_mv_ids,
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
        title: str,
        artist: str,
        background: bytes,
        preview: bytes,
        key_sounds: tuple[Pm3RomKeySound, ...] = (),
        custom_mv: Pm3RomMv | None = None,
    ) -> dict[str, bytes]:
        return self.build_many([
            Pm3RomSong(
                song_id=song_id,
                mv_id=mv_id,
                title=title,
                artist=artist,
                background=background,
                preview=preview,
                key_sounds=key_sounds,
                custom_mv=custom_mv,
            )
        ])

    def build_many(self, songs: list[Pm3RomSong]) -> dict[str, bytes]:
        custom_key_sounds: dict[str, bytes] = {}
        for song in songs:
            for key_sound in song.key_sounds:
                relative = self._key_sound_path(key_sound.relative_path)
                previous = custom_key_sounds.get(relative)
                if previous is not None and previous != key_sound.payload:
                    raise Pm3RomBuildError(f"自定义 Key 音路径冲突：{relative}")
                custom_key_sounds[relative] = key_sound.payload
        custom_mvs: dict[int, bytes] = {}
        for song in songs:
            if song.custom_mv is None:
                continue
            if song.custom_mv.mv_id < 20 or song.custom_mv.mv_id > 99:
                raise Pm3RomBuildError("自定义 PM3 MV ID 必须在 20..99")
            previous = custom_mvs.get(song.custom_mv.mv_id)
            if previous is not None and previous != song.custom_mv.payload:
                raise Pm3RomBuildError(
                    f"自定义 MV {song.custom_mv.mv_id} 被多个不同文件重复使用"
                )
            custom_mvs[song.custom_mv.mv_id] = song.custom_mv.payload
        state = self.inspect_many(
            [song.song_id for song in songs],
            custom_mv_ids=list(custom_mvs),
        )
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
        ui_rom = self.workspace.resolve("game", "ROMS/ui.rom", expect="file")

        with tempfile.TemporaryDirectory(prefix="bmson2pm-pm3-rom-") as directory:
            temporary = Path(directory)
            lua_root = temporary / "lua_script"
            sound_root = temporary / "sound"
            ui_root = temporary / "ui"
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
            for relative, payload in custom_key_sounds.items():
                destination = sound_root.joinpath(*PurePosixPath(relative).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.is_symlink() or destination.is_dir():
                    raise Pm3RomBuildError(f"无法写入自定义 Key 音：{relative}")
                destination.write_bytes(payload)

            self._run(
                [unsquashfs, "-no-progress", "-d", str(ui_root), str(ui_rom)],
                "展开 ui.rom",
            )
            title_large: list[Pm3UiText] = []
            title_small: list[Pm3UiText] = []
            singers: list[Pm3UiText] = []
            for song in songs:
                reservation = reserved_ui(song.song_id)
                if reservation is None:
                    raise Pm3RomBuildError(
                        f"曲目 {song.song_id} 没有 PM3 UI 预留帧"
                    )
                title_large.append(Pm3UiText(
                    reservation["title_image"], song.title, "songB",
                ))
                title_small.append(Pm3UiText(
                    reservation["title_image"], song.title, "songS",
                ))
                singers.append(Pm3UiText(
                    reservation["singer_image"], song.artist, "singer",
                ))
            ui_replacements = {
                "song/songB.swf": title_large,
                "song/songS.swf": title_small,
                "singer/singer.swf": singers,
            }
            patched_ui_files: dict[str, bytes] = {}
            try:
                for relative, replacements in ui_replacements.items():
                    path = ui_root.joinpath(*PurePosixPath(relative).parts)
                    if not path.is_file():
                        raise Pm3RomBuildError(f"ui.rom 缺少 {relative}")
                    patched = patch_pm3_ui_swf(path.read_bytes(), replacements)
                    path.write_bytes(patched)
                    patched_ui_files[relative] = patched
            except Pm3UiError as exc:
                raise Pm3RomBuildError(str(exc)) from exc

            ui_mv6_root: Path | None = None
            if custom_mvs:
                ui_mv6_root = temporary / "ui_mv6"
                shutil.copytree(
                    self.workspace.resolve("game", "media/ui_mv6", expect="directory"),
                    ui_mv6_root,
                    symlinks=True,
                )
                for mv_id, payload in custom_mvs.items():
                    filename = f"mv{mv_id}.swf"
                    (ui_mv6_root / filename).write_bytes(payload)
                    self._replace_symlink(
                        ui_root / "mv" / filename,
                        f"../../ui_mv6/./{filename}",
                    )

            builds = [
                (lua_root, output / "lua_script.rom"),
                (sound_root, output / "sound.rom"),
                (ui_root, output / "ui.rom"),
            ]
            for bundle in bundles:
                builds.extend((
                    (background_roots[bundle], output / f"SOUND_BG{bundle}.rom"),
                    (preview_roots[bundle], output / f"SOUND_PRE{bundle}.rom"),
                ))
            if ui_mv6_root is not None:
                builds.append((ui_mv6_root, output / "ui_mv6.rom"))
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
                    key_sounds=song.key_sounds,
                )
            self._verify_ui_contents(
                unsquashfs, output=output, expected=patched_ui_files,
            )
            for mv_id, payload in custom_mvs.items():
                self._verify_mv_contents(
                    unsquashfs,
                    output=output,
                    mv_id=mv_id,
                    payload=payload,
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

    @staticmethod
    def _key_sound_path(relative: str) -> str:
        normalized = relative.replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        path = PurePosixPath(normalized)
        if (
            path.is_absolute()
            or len(path.parts) != 2
            or path.parts[0].casefold() != "note"
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.suffix.casefold() != ".wav"
        ):
            raise Pm3RomBuildError(f"自定义 Key 音路径无效：{relative}")
        return path.as_posix()

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
        key_sounds: tuple[Pm3RomKeySound, ...],
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
        for key_sound in key_sounds:
            relative = self._key_sound_path(key_sound.relative_path)
            actual = self._run_bytes(
                [tool, "-cat", str(output / "sound.rom"), relative],
                f"回读 Key 音 {PurePosixPath(relative).name}",
            )
            if actual != key_sound.payload:
                raise Pm3RomBuildError(f"Key 音回读内容与输入不一致：{relative}")
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

    def _verify_mv_contents(
        self,
        tool: str,
        *,
        output: Path,
        mv_id: int,
        payload: bytes,
    ) -> None:
        filename = f"mv{mv_id}.swf"
        actual = self._run_bytes(
            [tool, "-cat", str(output / "ui_mv6.rom"), filename],
            f"回读自定义 MV {mv_id}",
        )
        if actual != payload:
            raise Pm3RomBuildError(f"自定义 MV {mv_id} 回读内容与输入不一致")
        listing = self._run_bytes(
            [tool, "-ll", str(output / "ui.rom")],
            "回读 ui.rom 链接",
        ).decode("utf-8", errors="replace")
        expected_link = f"mv/{filename} -> ../../ui_mv6/./{filename}"
        if expected_link not in listing:
            raise Pm3RomBuildError(f"ui.rom 缺少链接：{expected_link}")

    def _verify_ui_contents(
        self,
        tool: str,
        *,
        output: Path,
        expected: dict[str, bytes],
    ) -> None:
        for relative, payload in expected.items():
            actual = self._run_bytes(
                [tool, "-cat", str(output / "ui.rom"), relative],
                f"回读 PM3 UI {PurePosixPath(relative).name}",
            )
            if actual != payload:
                raise Pm3RomBuildError(f"ui.rom 回读内容不一致：{relative}")

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
