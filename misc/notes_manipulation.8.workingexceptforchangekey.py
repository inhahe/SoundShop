from webbrowser import get


modes = ["Ionian", "Dorian", "Phrygian", "Lydian", "Mixolydian", "Aeolian", "Locrian"]
modes_dict = dict(zip((mode.lower() for mode in modes), range(7)))

intervals = [2,1,2,2,1,2,2]  # Intervals from A to A

notes_dict = {}
semi = 0
letters = "ABCDEFG"
for note, i in zip(letters, intervals):  
  for a, acc in enumerate(("bb", "b", "", "#", "##")):
    print(f"{a=} {acc=} {semi+a-2=} {note+acc=}")
    notes_dict[note+acc] = semi+a-2
  for a, acc in enumerate(("♭♭", "♭", "", "♯", "♯♯")):
    notes_dict[note+acc] = semi+a-2
  semi += i

semitones_dict = {}
for key, value in notes_dict.items():

  print(f"{key=} {value=}")

  semitones_dict.setdefault(value, []).append(key)

def get_semitones(notes):
  if type(notes) is str:
    notes = notes.split()
  currentoctave = 4
  r = []
  for n in notes:
    if n.startswith("O"):
      currentoctave = int(n[1:])
    else:
      if n[-1].isdigit():
        note, octave = notes_dict[n[:-1]], int(n[-1])
      else:
        note, octave = notes_dict[n], currentoctave
      r.append((octave-4)*12+note)
  return r

def build_table(key):
  table = []
  semi = key
  for i in major_intervals:
    table.append(semi)
    semi = (semi+i)%12
  print(f"{table=}")
  return table

#interval_starts = [0, 0, 1, 2, 2, 3, 3, 4, 5, 5, 6, 6]

#a = f, c, g
#f = a b c# d e f# g#
#      2 2  1 2 2  2 1

major_intervals = [2, 2, 1, 2, 2, 2, 1]
scales = []
for n in range(12):
  cur_semi = n
  scale_s = set()
  scale_l = []
  #for i in intervals[interval_starts[n]:]+intervals[:interval_starts[n]]:  
  for i in major_intervals:
    scale_s.add(cur_semi)
    scale_l.append(cur_semi)
    cur_semi = (cur_semi+i)%12
  scales.append((scale_s, scale_l))

print(f"{scales=}")

def get_keys(semis):
  semis_s = set((semi%12 for semi in semis))
  modes = [list() for _ in range(7)]
  for scale_s, scale_l in scales:
    print(f"{scale_s=} {semis_s=}")
    if semis_s.issubset(scale_s):
      print("match")
      for n, semi in enumerate(scale_l):
        modes[n].append(semi)
  return modes


semitone_letters = "AABCCDDEFFGG"
def get_notes(semis, key, accidental=""):
  if type(key) is str:
    accidental = accidental or key[1:] or "#"
    key = notes_dict[key]
  table = build_table(key)
  accidental = accidental or "#"
  notes = {}
  l_ind = letters.index(semitone_letters[key])
  letters2 = letters[:7][l_ind:]+letters[:7][:l_ind]
  for semi, letter in zip(table, letters2):
    print(f"{semi=} {letter=}")
    print(f"{semitones_dict[semi]=}")
    for note in semitones_dict[semi]:
      if note[0]==letter:
        notes[semi] = note
        break
    semi = (semi+i)%12

  # Generate output for the given semitones
  
  print(f"{notes=}")
  
  result = []
  semi_mods = [semi%12 for semi in semis]
  print(f"{semi_mods=}")
  for semi_mod in semi_mods:
    octave = (semi // 12) + 4
    result.append(notes[semi_mod] + str(octave))
  return " ".join(result)

def change_key(semis, key1, mode1, key2, mode2):
  if type(mode1) is str:
    mode1 = modes_dict[mode1.lower()]
  if type(mode2) is str:
    mode2 = modes_dict[mode2.lower()]
  if type(key1) is str:
    key1 = notes_dict[key1]
  if type(key2) is str:
    key2 = notes_dict[key2]
  table1 = build_table(key1)
  table2 = build_table(key2)
  semis2 = []
  for semi in semis:
    octave, semi_mod = divmod(semi, 12)
    semis2.append(semi_mod+octave*12+table2[table1.index(semi_mod)])
  return semis2     
  #intervals = 2, 1, 2, 2, 1, 2, 2, 2


  #key1=3 mode1=0
  #semis = 3, 5, 7, 8, 10, 0, 2
  #intervals = 2, 2, 1, 2, 2, 2, 1

  #key=1 mode1=0
  #semis = Bb, C, D, Eb, F, G, A
  #semis = 1, 3, 5, 6, 8, 10, 12
  #intervals = 2, 2, 1, 2, 2, 2, 1


print(f"{get_keys(get_semitones(("A Bb C D E F G")))=}")

#print(change_key(get_semitones(("O4", "C", "D", "E", "F", "G", "O5", "A", "B")), 3, 0, 3, 1))
      
#print(get_notes(change_key(get_semitones(("O4", "C", "D", "E", "F", "G", "O5", "A", "B")), 3, 0, 3, 1), "C", "b"))

semitones = get_semitones("C D E F G A B")
print(f"{semitones=}")
changed_semitones = change_key(semitones, "C", "Ionian", "A", "Ionian")
print(f"{sorted(s%12 for s in changed_semitones)=}")

#should get F# G# A B C# D E
#gets 

print(f"{get_keys(changed_semitones)=}")
