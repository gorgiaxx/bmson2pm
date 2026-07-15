from __future__ import annotations

import difflib
import hashlib
import mimetypes
from pathlib import Path, PurePosixPath
from typing import Any

from ..adapters.pm3 import Pm3Adapter, Pm3FormatError
from ..adapters.pm3_crypto import Pm3CryptoError, decrypt_song_list
from ..adapters.pm3_cut_table import cut_for as _lookup_cut
from ..adapters.pm3_parser import Pm3SongInfo, parse_song_list
from ..config import pm3_root


class Pm3WorkspaceError(ValueError):
    pass


DEFAULT_ROOTS = {
    "game": "游戏只读目录",
    "rewrite": "下载覆盖目录",
}


class Pm3Workspace:
    MAX_PREVIEW = 256 * 1024
    MAX_DIFF = 2 * 1024 * 1024
    MAX_HASH = 64 * 1024 * 1024

    def __init__(self, roots: dict[str, Path] | None = None) -> None:
        if roots is None:
            roots = {}
            for root_id in DEFAULT_ROOTS:
                path = pm3_root(root_id)
                if path is not None:
                    roots[root_id] = path
        self.roots = {root_id: path.expanduser() for root_id, path in roots.items()}
        self.labels = {
            **DEFAULT_ROOTS,
            **{root_id: root_id for root_id in self.roots if root_id not in DEFAULT_ROOTS},
        }
        self.adapter = Pm3Adapter()
        self._catalog_rows: list[tuple[Pm3SongInfo, str, str]] | None = None
        self._catalog_warnings: list[str] = []

    def root_descriptors(self) -> list[dict[str, Any]]:
        ordered = list(DEFAULT_ROOTS)
        ordered += [root_id for root_id in self.roots if root_id not in DEFAULT_ROOTS]
        return [
            {
                "id": root_id,
                "label": self.labels.get(root_id, root_id),
                "available": root_id in self.roots and self.roots[root_id].is_dir(),
                "read_only": True,
            }
            for root_id in ordered
        ]

    def resolve(self, root_id: str, relative_path: str = "", *, expect: str | None = None) -> Path:
        if root_id not in self.roots:
            raise Pm3WorkspaceError("未知的 PM3 受信目录")
        root = self.roots[root_id].resolve()
        if not root.is_dir():
            raise Pm3WorkspaceError(f"受信目录 {root_id} 当前不可用")
        pure = PurePosixPath(relative_path or ".")
        if pure.is_absolute() or ".." in pure.parts:
            raise Pm3WorkspaceError("路径必须位于选定的只读目录内")
        candidate = (root / Path(*pure.parts)).resolve()
        if not candidate.is_relative_to(root):
            raise Pm3WorkspaceError("路径越过了只读目录边界")
        if expect == "file" and not candidate.is_file():
            raise Pm3WorkspaceError("文件不存在或不是普通文件")
        if expect == "directory" and not candidate.is_dir():
            raise Pm3WorkspaceError("目录不存在")
        return candidate

    def list_directory(self, root_id: str, relative_path: str = "") -> dict[str, Any]:
        directory = self.resolve(root_id, relative_path, expect="directory")
        root = self.roots[root_id].resolve()
        try:
            entries = list(directory.iterdir())
        except OSError as exc:
            raise Pm3WorkspaceError(f"无法读取目录：{exc}") from exc
        entries.sort(key=lambda path: (not path.is_dir(), path.name.lower()))
        limited = entries[:1000]
        return {
            "root_id": root_id,
            "path": self._relative(root, directory),
            "parent": self._parent_path(root, directory),
            "truncated": len(entries) > len(limited),
            "entries": [self._entry(root, path) for path in limited],
        }

    def inspect_file(
        self,
        root_id: str,
        relative_path: str,
        *,
        offset: int = 0,
        length: int = 4096,
    ) -> dict[str, Any]:
        path = self.resolve(root_id, relative_path, expect="file")
        size = path.stat().st_size
        offset = max(0, min(offset, size))
        length = max(16, min(length, 65536))
        with path.open("rb") as stream:
            stream.seek(offset)
            payload = stream.read(length)
        result: dict[str, Any] = {
            "root_id": root_id,
            "path": relative_path,
            "name": path.name,
            "size": size,
            "offset": offset,
            "length": len(payload),
            "format": self._format(path, payload if offset == 0 else b""),
            "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "sha256": self._hash(path) if size <= self.MAX_HASH else None,
            "hex_rows": self._hex_rows(payload, offset),
            "has_previous": offset > 0,
            "has_next": offset + len(payload) < size,
        }
        text = self._decode_text(payload)
        if text:
            result["text"] = text[0]
            result["encoding"] = text[1]
        return result

    def diff_files(self, left: dict[str, str], right: dict[str, str]) -> dict[str, Any]:
        left_path = self.resolve(left["root_id"], left["path"], expect="file")
        right_path = self.resolve(right["root_id"], right["path"], expect="file")
        left_size = left_path.stat().st_size
        right_size = right_path.stat().st_size
        with left_path.open("rb") as stream:
            left_data = stream.read(self.MAX_DIFF + 1)
        with right_path.open("rb") as stream:
            right_data = stream.read(self.MAX_DIFF + 1)
        truncated = len(left_data) > self.MAX_DIFF or len(right_data) > self.MAX_DIFF
        left_data = left_data[:self.MAX_DIFF]
        right_data = right_data[:self.MAX_DIFF]
        limit = min(len(left_data), len(right_data))
        differing = [index for index in range(limit) if left_data[index] != right_data[index]]
        changed = len(differing) + abs(len(left_data) - len(right_data))
        first_offsets = differing[:64]
        if len(left_data) != len(right_data) and len(first_offsets) < 64:
            first_offsets.append(limit)
        left_text = self._decode_text(left_data)
        right_text = self._decode_text(right_data)
        unified: list[str] | None = None
        if left_text and right_text:
            unified = list(difflib.unified_diff(
                left_text[0].splitlines(),
                right_text[0].splitlines(),
                fromfile=f"{left['root_id']}:{left['path']}",
                tofile=f"{right['root_id']}:{right['path']}",
                lineterm="",
                n=3,
            ))[:1200]
        windows = [
            {
                "offset": offset,
                "left": left_data[max(0, offset - 8):offset + 9].hex(" "),
                "right": right_data[max(0, offset - 8):offset + 9].hex(" "),
            }
            for offset in first_offsets[:12]
        ]
        return {
            "left": {**left, "size": left_size, "sha256": self._hash(left_path) if left_size <= self.MAX_HASH else None},
            "right": {**right, "size": right_size, "sha256": self._hash(right_path) if right_size <= self.MAX_HASH else None},
            "identical": left_size == right_size and not differing and not truncated,
            "compared_bytes": max(len(left_data), len(right_data)),
            "changed_bytes": changed,
            "first_differing_offsets": first_offsets,
            "windows": windows,
            "text_diff": unified,
            "truncated": truncated,
        }

    def catalog(self, search: str = "", *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        rows = self._load_catalog()
        needle = search.strip().casefold()
        if needle:
            rows = [
                row for row in rows
                if needle in " ".join((row[0].song_name, row[0].singer_name, row[0].filename, str(row[0].song_id))).casefold()
            ]
        offset = max(0, offset)
        limit = max(1, min(limit, 1000))
        page = rows[offset:offset + limit]
        return {
            "total": len(rows),
            "offset": offset,
            "limit": limit,
            "warnings": self._catalog_warnings,
            "records": [
                {**info.as_dict(), "root_id": root_id, "path": path, "available": True}
                for info, root_id, path in page
            ],
        }

    def inspect_chart(self, root_id: str, relative_path: str) -> dict[str, Any]:
        path, payload, cut, song_info = self.load_chart(root_id, relative_path)
        game_root = self.roots.get("game")
        result = self.adapter.inspect(
            payload,
            filename=path.name,
            cut_data=cut,
            song_info=song_info,
            game_root=game_root if game_root and game_root.is_dir() else None,
        )
        result["root_id"] = root_id
        result["path"] = relative_path
        return result

    def import_chart(
        self,
        root_id: str,
        relative_path: str,
        difficulty: Any,
    ):
        path, payload, cut, song_info = self.load_chart(root_id, relative_path)
        source_ref = {"root_id": root_id, "path": relative_path, "read_only": True}
        game_root = self.roots.get("game")
        return self.adapter.parse_with_warnings(
            payload,
            difficulty,
            filename=path.name,
            cut_data=cut,
            song_info=song_info,
            source_ref=source_ref,
            game_root=game_root if game_root and game_root.is_dir() else None,
        )

    def load_chart(
        self,
        root_id: str,
        relative_path: str,
    ) -> tuple[Path, bytes, bytes | None, Pm3SongInfo | None]:
        path = self.resolve(root_id, relative_path, expect="file")
        if path.suffix.lower() not in {".enc", ".enccut", ".txt"}:
            raise Pm3WorkspaceError("仅可按 PM3 谱面打开 .enc、.enccut 或明文 .txt")
        if path.stat().st_size > 2 * 1024 * 1024:
            raise Pm3WorkspaceError("PM3 谱面文件不得超过 2 MB")
        payload = path.read_bytes()
        cut = None
        if path.suffix.lower() == ".enccut":
            cut = self.cut_for(path.stem)
        song_info = self.song_info_for(path.stem)
        return path, payload, cut, song_info

    def cut_for(self, filename_stem: str) -> bytes:
        try:
            return _lookup_cut(filename_stem)
        except KeyError as exc:
            raise Pm3WorkspaceError(f"cut table 中没有 {filename_stem}") from exc

    def song_info_for(self, filename_stem: str) -> Pm3SongInfo | None:
        lowered = filename_stem.lower()
        return next((info for info, _, _ in self._load_catalog() if info.filename.lower() == lowered), None)

    def load_song_list_source(self) -> dict[str, Any]:
        for root_id, relative in (
            ("rewrite", "script_download/SongList.enc"),
            ("game", "media/script_AES/SongList.enc"),
        ):
            try:
                path = self.resolve(root_id, relative, expect="file")
                payload = path.read_bytes()
                decrypted = decrypt_song_list(payload)
                rows, warnings, encoding = parse_song_list(decrypted.plaintext)
                return {
                    "root_id": root_id,
                    "path": relative,
                    "payload": payload,
                    "plaintext": decrypted.plaintext,
                    "header": decrypted.header,
                    "slot": decrypted.slot,
                    "rows": rows,
                    "warnings": warnings,
                    "encoding": encoding,
                }
            except (Pm3WorkspaceError, Pm3CryptoError, OSError, ValueError):
                continue
        raise Pm3WorkspaceError("找不到可用于重建的 PM3 SongList.enc")

    def _load_catalog(self) -> list[tuple[Pm3SongInfo, str, str]]:
        if self._catalog_rows is not None:
            return self._catalog_rows
        candidates = [
            ("rewrite", "script_download/SongList.enc"),
            ("game", "media/script_AES/SongList.enc"),
        ]
        parsed: list[Pm3SongInfo] | None = None
        warnings: list[str] = []
        for root_id, relative in candidates:
            try:
                path = self.resolve(root_id, relative, expect="file")
                decrypted = decrypt_song_list(path.read_bytes())
                parsed, parse_warnings, _ = parse_song_list(decrypted.plaintext)
                warnings.extend(parse_warnings)
                break
            except (Pm3WorkspaceError, Pm3CryptoError, OSError, ValueError) as exc:
                warnings.append(f"{root_id}:{relative}：{exc}")
        if parsed is None:
            raise Pm3WorkspaceError("找不到可解密的 PM3 SongList.enc")
        rows: list[tuple[Pm3SongInfo, str, str]] = []
        missing = 0
        for info in parsed:
            rewrite = f"script_download/{info.filename}.enc"
            builtin = f"media/script_AES/{info.filename}.enccut"
            if self._exists("rewrite", rewrite):
                rows.append((info, "rewrite", rewrite))
            elif self._exists("game", builtin):
                rows.append((info, "game", builtin))
            else:
                missing += 1
        if missing:
            warnings.append(f"SongList 中 {missing} 条谱面记录没有对应文件")
        self._catalog_rows = rows
        self._catalog_warnings = warnings
        return rows

    def _exists(self, root_id: str, relative: str) -> bool:
        try:
            return self.resolve(root_id, relative, expect="file").is_file()
        except Pm3WorkspaceError:
            return False

    @staticmethod
    def _relative(root: Path, path: Path) -> str:
        value = path.relative_to(root).as_posix()
        return "" if value == "." else value

    @staticmethod
    def _parent_path(root: Path, path: Path) -> str | None:
        if path == root:
            return None
        value = path.parent.relative_to(root).as_posix()
        return "" if value == "." else value

    def _entry(self, root: Path, path: Path) -> dict[str, Any]:
        try:
            stat = path.stat()
            is_directory = path.is_dir()
            return {
                "name": path.name,
                "path": self._relative(root, path),
                "type": "directory" if is_directory else "file",
                "size": None if is_directory else stat.st_size,
                "modified_at": stat.st_mtime,
                "format": "directory" if is_directory else self._format(path),
                "role": self._role(path),
            }
        except OSError:
            return {"name": path.name, "path": self._relative(root, path), "type": "unreadable", "size": None, "format": "unknown", "role": "unknown"}

    @staticmethod
    def _format(path: Path, head: bytes = b"") -> str:
        suffix = path.suffix.lower()
        if suffix == ".enccut":
            return "pm3-enccut"
        if suffix == ".enc":
            return "pm3-encrypted"
        if head.startswith(b"hsqs"):
            return "squashfs"
        if head.startswith(b"\x7fELF"):
            return "elf"
        if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
            return "wav"
        if head.startswith(b"OggS"):
            return "ogg"
        if head[:3] in {b"FWS", b"CWS", b"ZWS"}:
            return "swf"
        return suffix.lstrip(".") or "binary"

    @staticmethod
    def _role(path: Path) -> str:
        lower = path.as_posix().lower()
        if path.name.lower() == "songlist.enc":
            return "song-catalog"
        if path.suffix.lower() in {".enc", ".enccut"}:
            return "chart"
        if "/sound/bg/" in lower:
            return "background-audio"
        if "/sound/preview/" in lower:
            return "preview-audio"
        if "/sound/note/" in lower:
            return "key-sound"
        if "/ui/mv" in lower:
            return "mv"
        return "resource"

    @staticmethod
    def _hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _hex_rows(payload: bytes, base_offset: int) -> list[dict[str, Any]]:
        rows = []
        for offset in range(0, len(payload), 16):
            block = payload[offset:offset + 16]
            rows.append({
                "offset": base_offset + offset,
                "offset_hex": f"{base_offset + offset:08x}",
                "hex": " ".join(f"{value:02x}" for value in block),
                "ascii": "".join(chr(value) if 32 <= value < 127 else "." for value in block),
            })
        return rows

    @staticmethod
    def _decode_text(payload: bytes) -> tuple[str, str] | None:
        if not payload or b"\0" in payload[:4096]:
            return None
        for encoding in ("utf-8", "cp950"):
            try:
                text = payload.decode(encoding)
            except UnicodeDecodeError:
                continue
            printable = sum(char.isprintable() or char in "\r\n\t" for char in text)
            if text and printable / len(text) >= 0.85:
                return text, encoding
        return None
