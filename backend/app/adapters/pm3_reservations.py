from __future__ import annotations

from typing import Final

from ..models import DifficultyId


# Verified against the p3 and p4 SongList, built-in chart names, background
# audio, preview audio and song UI assets. IDs 6, 84, 133, 146, 200 and 203 are
# not listed by the current SongList, but still have original PM3 assets and
# must not be reused. In particular, ID 133 retains p133.wav and therefore
# selects an unrelated preview even though its background/chart rows are gone.
PM3_RESERVED_SONG_IDS: Final[tuple[int, ...]] = (
    134, 150, 153, 154, 155, 156, 157,
)

# The selection UI renders names from fixed SWF timelines instead of the text
# columns in SongList.enc. These are unused singer frames and the bitmap
# character IDs belonging to the verified unused song frames. Keeping this
# mapping beside the key-slot reservations prevents the CSV and UI resources
# from drifting apart.
PM3_RESERVED_UI: Final[dict[int, dict[str, int]]] = {
    134: {"title_image": 387, "singer_id": 6, "singer_image": 16},
    150: {"title_image": 419, "singer_id": 16, "singer_image": 46},
    153: {"title_image": 425, "singer_id": 17, "singer_image": 49},
    154: {"title_image": 427, "singer_id": 18, "singer_image": 52},
    155: {"title_image": 429, "singer_id": 20, "singer_image": 58},
    156: {"title_image": 431, "singer_id": 21, "singer_image": 61},
    157: {"title_image": 433, "singer_id": 22, "singer_image": 64},
}


def slot_for_reserved_song(song_id: int) -> int | None:
    """Return bmson2pm's stable OTA slot for a verified unused song ID.

    Downloaded .enc charts carry their own slot selector in the file header;
    PM3 does not derive the slot from the song ID. We use song_id % 10 as a
    transparent, deterministic convention and use the same slot for every
    difficulty belonging to one song.
    """
    if song_id not in PM3_RESERVED_SONG_IDS:
        return None
    return song_id % 10


PM3_RESERVED_SONG_SLOTS: Final[dict[int, dict[DifficultyId, int]]] = {
    song_id: {
        difficulty: song_id % 10
        for difficulty in DifficultyId
    }
    for song_id in PM3_RESERVED_SONG_IDS
}


def reserved_slot(song_id: int, difficulty: DifficultyId) -> int | None:
    return PM3_RESERVED_SONG_SLOTS.get(song_id, {}).get(difficulty)


def reserved_ui(song_id: int) -> dict[str, int] | None:
    value = PM3_RESERVED_UI.get(song_id)
    return dict(value) if value is not None else None


def reservation_catalog() -> list[dict[str, object]]:
    return [
        {
            "song_id": song_id,
            "singer_id": PM3_RESERVED_UI[song_id]["singer_id"],
            "slots": {
                difficulty.value: slot
                for difficulty, slot in slots.items()
            },
        }
        for song_id, slots in PM3_RESERVED_SONG_SLOTS.items()
    ]
