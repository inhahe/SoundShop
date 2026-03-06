from __future__ import annotations

import re
import types
from fractions import Fraction

use_unicode_accidentals = False

note_re = re.compile(r"([a-zA-Z])(♯|♯♯|♭|♭♭|#|##|b|bb|)(-1|[0-9]|)")
noteoro_re = re.compile(r"([a-zA-Z](?:♯|♯♯|♭|♭♭|#|##|b|bb|)(?:-1|[0-9]|))|[Oo](-1|[0-9])")

letters = "CDEFGAB"
intervals = [2, 2, 1, 2, 2, 2, 1]
start_dict = {
    'A': '', 'Ab': 'b', 'B': '', 'Bb': 'b', 'C#': '#', 'Cb': 'b',
    'D': '', 'D#': '#', 'Db': 'b', 'E': '', 'Eb': 'b', "F#": '#',
    'F': '', 'G': '', 'Gb': 'b',
}
modes = ["Ionian", "Dorian", "Phrygian", "Lydian", "Mixolydian", "Aeolian", "Locrian"]
modes_dict = dict(zip((mode.lower() for mode in modes), range(7)))
notes_dict = {}
semitones_dict = {}
key_tables = {}

extra_scales = {
    'acoustic': [2, 14, 2, 1, 2, 1, -10], 'aeolian': [2, 13, 2, 2, 1, 2, -10],
    'algerian': [2, 13, 3, 1, 1, 3, -11, 2, 13, 2], 'super locrian': [1, 14, 1, 2, 2, 2, -10],
    'augmented': [15, 1, 3, 1, 3, -11], 'bebop dominant': [2, 14, 1, 2, 2, 1, 1, -11],
    'blues': [15, 2, 1, 1, 3, -10], 'chromatic': [1, 1, 13, 1, 1, 1, 1, 1, 1, 1, 1, -11],
    'dorian': [2, 13, 2, 2, 2, 1, -10], 'double harmonic': [1, 15, 1, 2, 1, 3, -11],
    'enigmatic': [1, 15, 2, 2, 2, 1, -11], 'flamenco': [1, 15, 1, 2, 1, 3, -11],
    'romani': [2, 13, 3, 1, 1, 2, -10], 'half-diminished': [2, 13, 2, 1, 2, 2, -10],
    'harmonic major': [2, 14, 1, 2, 1, 3, -11], 'harmonic minor': [2, 13, 2, 2, 1, 3, -11],
    'hijaroshi': [16, 2, 1, 4, -11], 'hungarian minor': [2, 13, 3, 1, 1, 3, -11],
    'hungarian major': [15, 1, 2, 1, 2, 1, -10], 'in': [1, 16, 2, 1, -8],
    'insen': [1, 16, 2, 3, -10], 'ionian': [2, 14, 1, 2, 2, 2, -11],
    'iwato': [1, 16, 1, 4, -10], 'locrian': [1, 14, 2, 1, 2, 2, -10],
    'lydian augmented': [2, 14, 2, 2, 1, 2, -11], 'lydian': [2, 14, 2, 1, 2, 2, -11],
    'locrian major': [2, 14, 1, 1, 2, 2, -10], 'pentatonic major': [2, 14, 3, 2, -9],
    'melodic minor ascending': [2, 13, 2, 2, 2, 2, -11],
    'melodic minor descending': [2, 13, 2, 2, 2, 2, -11],
    'pentatonic minor': [15, 2, 2, 3, -10], 'mixolydian': [2, 14, 1, 2, 2, 1, -10],
    'neapolitan major': [1, 14, 2, 2, 2, 2, -11], 'neapolitan minor': [1, 14, 2, 2, 1, 3, -11],
    'octatonic c-d': [2, 13, 2, 1, 2, 1, 2, -11], 'octatonic c-c#': [1, 14, 1, 2, 1, 2, 1],
    'persian': [1, 15, 1, 1, 2, 3, -11], 'phrygian dominant': [1, 15, 1, 2, 1, 2, -10],
    'phrygian': [1, 14, 2, 2, 1, 2, -10], 'prometheus': [2, 14, 2, 3, 1, -10],
    'harmonics': [15, 1, 1, 2, 2, -9], 'tritone': [1, 15, 2, 1, 3, -10],
    'two-semitone tritone': [1, 1, 16, 1, 1, -8], 'ukranian dorian': [2, 13, 3, 1, 2, 1, -10],
    'whole-tone scale': [2, 14, 2, 2, 2, -10], 'yo': [15, 2, 2, 3, -10],
}


def convert_accidental(accidental, use_unicode=use_unicode_accidentals):
    if accidental is None:
        return accidental
    if isinstance(accidental, Note):
        if use_unicode:
            accidental.accidental = accidental.accidental.replace("#", "♯").replace("b", "♭")
        else:
            accidental.accidental = accidental.accidental.replace("♯", "#").replace("♭", "b")
        return accidental
    if use_unicode:
        return accidental.replace("#", "♯").replace("b", "♭")
    else:
        return accidental.replace("♯", "#").replace("♭", "b")


def velocity(func):
    return lambda n: Note(n, velocity=min(max(func(n.velocity), 127), 0))


class NotesNode:
    def __init__(self, function=None):
        self.function = function


class NotesFilter:
    def __init__(self, function=None):
        self.function = function


class NotesGraph:
    def __init__(self, connections=None, filters=None):
        self.connections = connections or []
        self.filters = filters or []

    def addfilter(self, filter):
        if isinstance(filter, types.FunctionType):
            self.filters.append(NotesFilter(filter))
        elif isinstance(filter, NotesFilter):
            self.filters.append(filter)
        else:
            raise ValueError

    def addconnection(self, connection1, connection2):
        self.connections.append((connection1, connection2))

    def removeconnection(self, connection1, connection2):
        self.connections.remove((connection1, connection2))


def _to_frac(v):
    """Coerce a value to Fraction. Accepts Fraction, tuple (num, denom), int, float, or None."""
    if v is None or isinstance(v, Fraction):
        return v
    if isinstance(v, tuple):
        return Fraction(v[0], v[1])
    return Fraction(v)


class Note:
    def __init__(self, note=None, accidental=None, octave=None, key=None, mode=0,
                 degree=None, velocity=127, beatDuration=1, beatNumber=None,
                 beatInterval=None, playOrder=None, sampleNumber=None,
                 sampleDuration=None, timeOffset=None, timeDuration=None,
                 sampleOffset=None, timeInterval=None, sampleInterval=None,
                 bpm=None):
        assert not (beatInterval and beatNumber)
        beatDuration = _to_frac(beatDuration)
        beatNumber = _to_frac(beatNumber)
        beatInterval = _to_frac(beatInterval)
        self.beatInterval = beatInterval
        self.playOrder = playOrder
        self.octave = octave
        accidental = convert_accidental(accidental)
        self.accidental = accidental or ""
        self.letter = None
        self.midi = None
        self.key = key
        self.sampleNumber = sampleNumber
        self.sampleDuration = sampleDuration
        self.beatNumber = beatNumber
        self.beatDuration = beatDuration
        self.timeOffset = timeOffset
        self.timeDuration = timeDuration
        self.sampleOffset = sampleOffset
        self.timeInterval = timeInterval
        self.sampleInterval = sampleInterval
        self.velocity = velocity
        self.time = None
        self.note = None
        self.pitch_class = None
        self.degree = degree
        self.mode = mode

        if isinstance(key, str):
            key = Note(key).pitch_class
        elif isinstance(key, Note):
            key = key.pitch_class

        if isinstance(note, str):
            m = note_re.match(note)
            self.letter, self.accidental, octave2 = m.group(1, 2, 3)
            if octave is not None:
                self.octave = octave
            elif octave2:
                self.octave = int(octave2)
            else:
                self.octave = 4
        elif isinstance(note, Note):
            self.octave = note.octave if octave is None else octave
            self.midi = note.midi
            self.letter = note.letter
            self.key = key if key else note.key
            self.accidental = note.accidental
            self.velocity = note.velocity
            self.beatDuration = note.beatDuration
        elif isinstance(note, int):
            if octave is None:
                self.midi = note
            else:
                self.midi = note % 12 + octave * 12

        if self.octave is None:
            if self.letter is not None:
                self.octave = 4 if self.letter < "C" else 5
            elif octave is not None:
                self.octave = octave
            else:
                self.octave = 4

        if note is None:
            if key is None or degree is None:
                raise ValueError("Can't infer note because key and/or degree isn't set")
            self.midi = key_tables[convert_accidental(key, False)][mode][degree] + self.octave * 4
            self.letter = Note(self.midi, key=key, mode=mode).letter

        if self.letter is not None:
            self.note = self.letter + self.accidental + str(self.octave)
            self.midi = notes_dict.get(self.note, self.midi)

        self.key = key
        if self.letter:
            self.pitch_class = self.letter + self.accidental

        if isinstance(mode, str):
            self.mode = modes_dict[mode]
        else:
            self.mode = mode

        if key is not None and self.midi is not None:
            table = build_table(key, mode)
            semi = self.midi % 12
            if semi in table:
                self.degree = table.index(semi)

    def __str__(self):
        return self.note or str(self.midi)

    def __repr__(self):
        return self.note or str(self.midi)


class Notes(list):
    def __init__(self, notes=None, key=None, bpm=None, mode=0, accidental=None,
                 sampleOffset=None, beatNumber=None, timeOffset=None,
                 sampleDuration=None, beatDuration=None, timeDuration=None,
                 orderOffset=0):
        super().__init__()
        self.key = key
        self.mode = mode
        self.bpm = bpm
        self.accidental = accidental
        self.beatNumber = _to_frac(beatNumber)
        self.timeOffset = timeOffset
        self.sampleOffset = sampleOffset
        self.sampleDuration = sampleDuration
        self.beatDuration = _to_frac(beatDuration)
        self.timeDuration = timeDuration

        if notes is None:
            return

        if isinstance(notes, str):
            octave = None
            for m in noteoro_re.findall(notes):
                if m[0]:
                    self.append(Note(m[0], octave=octave, key=key, bpm=bpm,
                                     accidental=accidental, mode=mode))
                else:
                    octave = int(m[1])
        elif isinstance(notes, (Notes, list)):
            for note in notes:
                if note.playOrder is not None:
                    note.playOrder += orderOffset
                if sampleOffset and note.sampleNumber:
                    note.sampleNumber += sampleOffset
                self.append(note)


def merge_notes(*notess):
    merged = []
    for x in notess:
        if isinstance(x, Notes):
            merged.extend(x)
        else:
            merged.extend(Notes(x))
    return sorted(merged, key=lambda y: (y.sampleNumber or 0, y.playOrder or 0))


def make_tables():
    semi = -2
    i = -2
    while semi < 129:
        interval = intervals[i % 7]
        stro = str((semi - 12) // 12)
        letter = letters[i % 7]
        for n, acc in enumerate(("bb", "b", "", "#", "##")):
            notes_dict[letter + acc + stro] = semi + n - 2
            semitones_dict.setdefault(semi + n - 2, []).append(letter + acc + stro)
        for n, acc in enumerate(("♭♭", "♭", "", "♯", "♯♯")):
            notes_dict[letter + acc + stro] = semi + n - 2
        semi += interval
        i += 1

    semi = 0
    for interval, letter in zip(intervals, letters):
        for n, acc in enumerate(("bb", "b", "", "#", "##")):
            notes_dict[letter + acc] = semi + n - 2
        for n, acc in enumerate(("♭♭", "♭", "", "♯", "♯♯")):
            notes_dict[letter + acc] = semi + n - 2
        semi += interval
        i += 1

    for key, value in semitones_dict.items():
        semitones_dict[key] = [Note(n) for n in value]

    for key in start_dict:
        mode_tables = []
        for mode in range(7):
            mode_tables.append(build_notes(key, mode, start_dict[key]))
        key_tables[key] = mode_tables


def build_table(key, mode=0):
    key = Note(key)
    if isinstance(mode, str):
        mode = modes_dict[mode.lower()]
    table = []
    semi = key.midi % 12
    for i in intervals[mode:] + intervals[:mode]:
        table.append(semi)
        semi = (semi + i) % 12
    return table


scales = []
for _n in range(12):
    _cur_semi = _n
    _scale_s = set()
    _scale_l = []
    for _i in intervals:
        _scale_s.add(_cur_semi)
        _scale_l.append(_cur_semi)
        _cur_semi = (_cur_semi + _i) % 12
    scales.append((_scale_s, _scale_l))


def get_keys(notes):
    if isinstance(notes, str):
        notes = notes.split()
    semis_s = set(Note(note).midi % 12 for note in notes)
    result_modes = [list() for _ in range(7)]
    for scale_s, scale_l in scales:
        if semis_s.issubset(scale_s):
            for n, semi in enumerate(scale_l):
                result_modes[n].append(semi)
    found_scales = []
    min_semis = min(semis_s)
    max_semis = max(semis_s)
    semis_min = set(semi - min_semis for semi in semis_s)
    semis_key = min_semis % 12
    max_semis -= min_semis
    for scale_name, scale_intervals in extra_scales.items():
        found_keys = []
        for key in range(len(scale_intervals)):
            scale = []
            note = key
            for idx in range(max(len(semis_min), len(scale))):
                interval = scale_intervals[idx % len(scale_intervals)]
                scale.append(note)
                note += idx
            if semis_min.issubset(set(scale)):
                found_keys.append(key + semis_key)
        if found_keys:
            found_scales.append((scale_name, found_keys))
    return result_modes, found_scales


def build_notes(key, mode, accidental):
    notes = {}
    key = Note(key)
    first_letter = key.letter
    table = build_table(key, mode)
    backup_first = None
    if not first_letter:
        for note in semitones_dict[key.midi]:
            if accidental is None and note.accidental == "#":
                backup_first = note[0]
            if note.accidental == (accidental or ""):
                first_letter = note.letter
                break
        else:
            if accidental is None:
                if backup_first is None:
                    raise ValueError(f"No whole or sharp note found for semitone {key}")
                else:
                    first_letter = backup_first
            else:
                if accidental:
                    raise ValueError(f"No {accidental} note found for semitone {key}")
                else:
                    raise ValueError(f"No whole note found for semitone {key}")
    lifl = letters.index(first_letter)
    for semi, letter in zip(table, letters[lifl:] + letters[:lifl]):
        for note in semitones_dict[semi]:
            if note.letter == letter:
                notes[semi] = note
                break
        else:
            raise ValueError(
                f"No note found for semitone {semi} letter {letter} "
                f"key {key.letter + key.accidental} mode {mode}"
            )
    return notes


def get_notes(notes, key=None, mode=0, accidental=None):
    if isinstance(notes, str):
        notes = notes.split()
    return [Note(note, key=key, mode=mode, accidental=accidental) for note in notes]


def change_key(notes, key1=None, mode1=None, key2=None, mode2=None):
    if isinstance(notes, str):
        notes = notes.split()
    if isinstance(mode1, str):
        mode1 = modes_dict[mode1.lower()]
    if isinstance(mode2, str):
        mode2 = modes_dict[mode2.lower()]
    if isinstance(key1, Note):
        key1 = key1.letter + key1.accidental
    if isinstance(key2, Note):
        key2 = key2.letter + key2.accidental
    semis2 = []
    if key1 is not None:
        table1 = key_tables[key1][mode1]
        table2 = key_tables[key2][mode2]
        for note in notes:
            if isinstance(note, Note):
                semi = note.midi
            elif isinstance(note, str):
                semi = notes_dict[note]
            elif isinstance(note, int):
                semi = note
            else:
                raise TypeError(f"Unexpected note type: {type(note)}")
            octave, semi_mod = divmod(semi, 12)
            try:
                semis2.append(octave * 12 + table2[table1.index(semi_mod)])
            except ValueError:
                raise ValueError(
                    f"No note found for semitone {semi_mod} in key {key1} mode {modes[mode1]}"
                )
    else:
        for note in notes:
            if note.key is None:
                raise ValueError(
                    f"Note {note} doesn't have a key and no key specified so can't infer degree"
                )
    return semis2


def shift_semitones(notes, x):
    results = []
    if isinstance(notes, str):
        notes = notes.split()
    for note in notes:
        if isinstance(note, str):
            semi = notes_dict[note]
        elif isinstance(note, Note):
            semi = note.midi
        elif isinstance(note, int):
            semi = note
        else:
            raise TypeError(f"Unexpected note type: {type(note)}")
        results.append(semi + x)
    return results


def shift_octaves(notes, octave):
    return shift_semitones(notes, octave * 12)


make_tables()
