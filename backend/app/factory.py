from .models import (
    CreateProjectRequest,
    DifficultyChart,
    DifficultyId,
    Lane,
    Metadata,
    SongProject,
    TimingMap,
)


DEFAULT_LANES = [
    Lane(id=1, code="small_left", display_name="左小鼓", color="#40c4b4", hand="left"),
    Lane(id=2, code="small_right", display_name="右小鼓", color="#e96978", hand="right"),
    Lane(id=3, code="rim_simultaneous", display_name="鼓缘同时击打", color="#62a6e8", hand="both"),
    Lane(id=4, code="rim_single", display_name="鼓缘单击", color="#dc84d8", hand="either"),
    Lane(id=5, code="head_simultaneous", display_name="鼓面同时击打", color="#f2aa4f", hand="both"),
    Lane(id=6, code="head_single", display_name="鼓面单击", color="#e9d35b", hand="either"),
]

DIFFICULTY_NAMES = {
    DifficultyId.easy: "初级",
    DifficultyId.normal: "中级",
    DifficultyId.hard: "高级",
    DifficultyId.special: "超高级",
    DifficultyId.master: "大师级",
}


def new_project(request: CreateProjectRequest | None = None) -> SongProject:
    request = request or CreateProjectRequest()
    difficulties = {
        difficulty: DifficultyChart(
            id=difficulty,
            display_name=name,
            level=index + 1,
        )
        for index, (difficulty, name) in enumerate(DIFFICULTY_NAMES.items())
    }
    return SongProject(
        metadata=Metadata(title=request.title, artist=request.artist),
        timing=TimingMap(initial_bpm=request.initial_bpm),
        lanes=[lane.model_copy(deep=True) for lane in DEFAULT_LANES],
        difficulties=difficulties,
        game_specific_data={"lane_semantics": "pm3-six-input-v2"},
    )
