import musical_scales, notes_manipulation
import pprint
scales = """acoustic
aeolian
algerian
super locrian
augmented
bebop dominant
blues
chromatic
dorian
double harmonic
enigmatic
flamenco
romani
half-diminished
harmonic major
harmonic minor
hijaroshi
hungarian minor
hungarian major
in
insen
ionian
iwato
locrian
lydian augmented
lydian
locrian major
pentatonic major
melodic minor ascending
melodic minor descending
pentatonic minor
mixolydian
neapolitan major
neapolitan minor
octatonic c-d
octatonic c-c#
persian
phrygian dominant
phrygian
prometheus
harmonics
tritone
two-semitone tritone
ukranian dorian
whole-tone scale
yo""".split("\n")
d = {}
print("-----------------------------------------------------------------------")
for scale in scales:
  try:
    notes = musical_scales.scale("A", scale)
    notes2 = notes_manipulation.get_semitones(note.midi for note in notes)
    lastnote = None
    intervals = []
    for note in notes2:
      if lastnote is not None:
        intervals.append(note-lastnote)
      lastnote = note
    if scale=="blues":
      print(f"{scale} {notes=} {notes2=} {intervals=}")

    d[scale] = intervals
  except musical_scales.MusicException as e:
    print(f"{scale=} {e=}")
#pprint.pprint(d)
