"""Choose and follow the primary subject across the samples of one scene.

Coordinates are normalised to the source frame (0..1). The output per sample is a
``Focus``: where the crop window should be centred, plus a region (face or group) that
should stay fully inside the crop whenever it fits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ytclip.smartcrop.detectors import Box, Detection

FACE_BONUS = 1.3
CONTINUITY_GAIN = 2.0
CONTINUITY_SIGMA = 0.12
GROUP_SCORE_RATIO = 0.5
GROUP_FIT = 0.95
FACE_TARGET_FROM_TOP = 0.38  # place faces ~38% down the crop, leaving headroom


@dataclass(frozen=True)
class Subject:
    box: Box
    score: float
    face: Box | None = None


@dataclass(frozen=True)
class Focus:
    x: float
    y: float
    keep: Box


def _face_owner(face: Box, bodies: list[Detection]) -> Detection | None:
    """Smallest person/animal box whose upper half contains the face centre."""
    owners = [
        d for d in bodies
        if d.kind in ("person", "animal") and d.box.contains_point(face.cx, face.cy)
        and face.cy <= d.box.y1 + d.box.height * 0.5
    ]
    return min(owners, key=lambda d: d.box.area, default=None)


def _face_to_body(face: Box) -> Box:
    """Approximate head-and-shoulders region for a face with no detected body."""
    return Box(face.x1 - face.width, face.y1 - face.height * 0.3,
               face.x2 + face.width, face.y2 + face.height * 2).clipped()


def _size_term(box: Box) -> float:
    return 0.4 + min(1.0, math.sqrt(box.area) * 2)


def build_subjects(detections: list[Detection]) -> list[Subject]:
    faces = [d for d in detections if d.kind == "face"]
    bodies = [d for d in detections if d.kind not in ("face", "motion")]
    motion = [d for d in detections if d.kind == "motion"]

    face_for: dict[int, Detection] = {}
    loose_faces: list[Detection] = []
    for face in faces:
        owner = _face_owner(face.box, bodies)
        if owner is None:
            loose_faces.append(face)
        elif id(owner) not in face_for or face.score > face_for[id(owner)].score:
            face_for[id(owner)] = face

    subjects = []
    for body in bodies:
        face = face_for.get(id(body))
        bonus = FACE_BONUS if face else 1.0
        subjects.append(Subject(body.box, body.weight * body.score * _size_term(body.box) * bonus,
                                face.box if face else None))
    for face in loose_faces:
        region = _face_to_body(face.box)
        subjects.append(Subject(region, face.score * _size_term(region) * FACE_BONUS, face.box))
    if not subjects:
        subjects = [Subject(d.box, d.weight * d.score * _size_term(d.box)) for d in motion]
    return subjects


def _similarity(a: Box, b: Box) -> float:
    distance = math.hypot(a.cx - b.cx, a.cy - b.cy)
    return max(a.iou(b), math.exp(-(distance ** 2) / (2 * CONTINUITY_SIGMA ** 2)))


def _pick_primary(subjects: list[Subject], previous: Box | None) -> Subject:
    if previous is None:
        return max(subjects, key=lambda s: s.score)
    return max(subjects, key=lambda s: s.score * (1 + CONTINUITY_GAIN * _similarity(s.box, previous)))


def _group_region(primary: Subject, subjects: list[Subject], crop_w: float, crop_h: float) -> Box:
    """Grow the framed region to include other strong subjects while it still fits."""
    region = primary.face or primary.box
    for other in sorted(subjects, key=lambda s: -s.score):
        if other is primary or other.score < primary.score * GROUP_SCORE_RATIO:
            continue
        candidate = region.union(other.face or other.box)
        if candidate.width <= crop_w * GROUP_FIT and candidate.height <= crop_h * GROUP_FIT:
            region = candidate
    return region


def _focus_for(primary: Subject, region: Box, crop_h: float) -> Focus:
    anchor = primary.face or region
    if region is not anchor:  # a group: centre on the whole group
        x = region.cx
    elif primary.face:
        x = 0.6 * primary.face.cx + 0.4 * primary.box.cx
    else:
        x = region.cx
    if primary.face and crop_h < 1:
        y = primary.face.cy + crop_h * (0.5 - FACE_TARGET_FROM_TOP)
    else:
        y = region.cy
    return Focus(x, y, region)


def track_segment(detections_per_sample: list[list[Detection]], crop_w: float,
                  crop_h: float) -> list[Focus | None]:
    """Return a focus per sample (None where nothing was detected)."""
    foci: list[Focus | None] = []
    previous: Box | None = None
    for detections in detections_per_sample:
        subjects = build_subjects(detections)
        if not subjects:
            foci.append(None)
            continue
        primary = _pick_primary(subjects, previous)
        region = _group_region(primary, subjects, crop_w, crop_h)
        foci.append(_focus_for(primary, region, crop_h))
        previous = primary.box
    return foci
