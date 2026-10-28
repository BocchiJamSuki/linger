"""Transcript data: segments, the hotkey's "what was just said" grouping, and autosave."""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

FINAL, PROVISIONAL, GAP, RESUME = 'final', 'provisional', 'gap', 'resume'
SPEECH = (FINAL, PROVISIONAL)

GROUP_GAP = 2.5    # Seconds of silence that separate one utterance from the next.
GROUP_LIMIT = 3    # Segments in a default group; the hotkey can extend beyond this.


@dataclass
class Segment:
    kind: str
    text: str
    start: float    # Wall-clock seconds (time.time()).
    end: float


def join_speech(texts):
    """Segments are pause-delimited fragments of speech: join with spaces, not newlines."""
    return ' '.join(t.strip() for t in texts if t.strip())


def recent_group(segments, partial=None, extra=0, gap=GROUP_GAP, limit=GROUP_LIMIT):
    """Indices of the latest utterance: trailing segments separated by short pauses.

    `partial` is (text, start) for words still being recognised; it counts as the newest
    piece and is reported as index -1. `extra` reaches that many segments further back.
    """
    picked = []
    newest_start = None
    if partial and partial[0].strip():
        picked.append(-1)
        newest_start = partial[1]
    for i in range(len(segments) - 1, -1, -1):
        seg = segments[i]
        if seg.kind not in SPEECH:
            if picked:
                break  # A connection gap or a restart ends the utterance.
            continue
        if picked and newest_start is not None and newest_start - seg.end > gap:
            break
        if len([p for p in picked if p >= 0]) >= limit:
            break
        picked.append(i)
        newest_start = seg.start
    if extra:
        earliest = min([p for p in picked if p >= 0], default=len(segments))
        for i in range(earliest - 1, -1, -1):
            if extra <= 0:
                break
            if segments[i].kind in SPEECH:
                picked.append(i)
                extra -= 1
    return sorted(picked, key=lambda i: (i < 0, i))


class Recorder:
    """Appends every finished line to a dated text file, so nothing is lost when the app closes."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.path = None

    def new_file(self):
        self.path = None

    def write(self, segment, note=''):
        try:
            if self.path is None:
                self.folder.mkdir(parents=True, exist_ok=True)
                stamp = datetime.fromtimestamp(segment.start)
                self.path = self._unique(self.folder / f'{stamp:%Y-%m-%d %H%M}.txt')
            clock = datetime.fromtimestamp(segment.start).strftime('%H:%M:%S')
            if segment.kind == FINAL:
                line = f'[{clock}] {segment.text}'
            elif segment.kind == PROVISIONAL:
                line = f'[{clock}] {segment.text}（未确认）'
            else:
                line = f'[{clock}] —— {note} ——'
            with self.path.open('a', encoding='utf-8') as f:
                f.write(line + '\n')
            return True
        except OSError:
            return False

    @staticmethod
    def _unique(path):
        n = 2
        candidate = path
        while candidate.exists():
            candidate = path.with_name(f'{path.stem} ({n}){path.suffix}')
            n += 1
        return candidate
