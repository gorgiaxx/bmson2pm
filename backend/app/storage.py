from __future__ import annotations

import json
import os
import shutil
from pathlib import Path, PurePosixPath

from .models import ProjectSummary, SongProject, utc_now


class ProjectNotFoundError(FileNotFoundError):
    pass


class ProjectAssetError(ValueError):
    pass


class ProjectStore:
    def __init__(self, root: Path | None = None) -> None:
        configured = os.getenv("BMSON2PM_DATA_DIR")
        self.root = Path(configured) if configured else (root or Path(__file__).parents[1] / "data" / "projects")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, project_id: str) -> Path:
        if not project_id or not all(char.isalnum() or char in "-_" for char in project_id):
            raise ProjectNotFoundError(project_id)
        return self.root / f"{project_id}.json"

    def _asset_root(self, project_id: str) -> Path:
        self._path(project_id)
        return self.root / "_assets" / project_id

    @staticmethod
    def _asset_relative_path(relative_path: str) -> PurePosixPath:
        cleaned = relative_path.replace("\\", "/")
        while cleaned.startswith("./"):
            cleaned = cleaned[2:]
        path = PurePosixPath(cleaned)
        if (
            not cleaned
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ProjectAssetError("项目资源路径无效")
        return path

    def save_asset(self, project_id: str, relative_path: str, payload: bytes) -> str:
        relative = self._asset_relative_path(relative_path)
        root = self._asset_root(project_id)
        target = root.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        temporary.write_bytes(payload)
        temporary.replace(target)
        return relative.as_posix()

    def asset_path(self, project_id: str, relative_path: str) -> Path:
        relative = self._asset_relative_path(relative_path)
        root = self._asset_root(project_id).resolve()
        target = root.joinpath(*relative.parts).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ProjectNotFoundError(relative_path)
        return target

    def delete_asset(self, project_id: str, relative_path: str) -> None:
        target = self.asset_path(project_id, relative_path)
        target.unlink()
        root = self._asset_root(project_id).resolve()
        parent = target.parent
        while parent != root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def clear_assets(self, project_id: str) -> None:
        root = self._asset_root(project_id)
        if root.exists():
            shutil.rmtree(root)

    def list(self) -> list[ProjectSummary]:
        summaries: list[ProjectSummary] = []
        for path in self.root.glob("*.json"):
            try:
                project = SongProject.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            summaries.append(
                ProjectSummary(
                    id=project.id,
                    title=project.metadata.title,
                    artist=project.metadata.artist,
                    updated_at=project.updated_at,
                    note_count=sum(len(chart.notes) for chart in project.difficulties.values()),
                )
            )
        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)

    def get(self, project_id: str) -> SongProject:
        path = self._path(project_id)
        if not path.exists():
            raise ProjectNotFoundError(project_id)
        return SongProject.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, project: SongProject, *, touch: bool = True) -> SongProject:
        saved = project.model_copy(deep=True)
        if touch:
            saved.updated_at = utc_now()
        target = self._path(saved.id)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(saved.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(target)
        return saved

    def delete(self, project_id: str) -> None:
        path = self._path(project_id)
        if not path.exists():
            raise ProjectNotFoundError(project_id)
        path.unlink()
        self.clear_assets(project_id)
