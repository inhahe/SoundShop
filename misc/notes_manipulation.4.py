modes = ["Ionian", "Dorian", "Phrygian", "Lydian", "Mixolydian", "Aeolian", "Locrian"]
modes_dict = dict(zip((mode.lower() for mode in modes), range(7)))

intervals = [2,1,2,2,1,2,2,2]  # Intervals from A to A

notes_dict = {}
semi = 0
letters = "ABCDEFGA"
for note, i in zip(letters, intervals):  
  for a, acc in enumerate(("bb", "b", "", "#", "##")):
    notes_dict[note+acc] = semi+a-2
  for a, acc in enumerate(("♭♭", "♭", "", "♯", "♯♯")):
    notes_dict[note+acc] = semi+a-2
  semi += i

semitones_dict = {}
for key, value in notes_dict.items():
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

scales = []
for n in range(12):
  cur_semi = n
  scale_s = set()
  scale_l = []
  for i in intervals:  # Use standard intervals for scales
    scale_s.add(cur_semi)
    scale_l.append(cur_semi)
    cur_semi = (cur_semi+i)%12
  scales.append((scale_s, scale_l))

def get_keys(semis):
  semis_s = set(semis)
  modes = [list() for _ in range(7)]
  for scale_s, scale_l in scales:
    if semis_s.issubset(scale_s):
      for n, semi in enumerate(scale_l):
        modes[n].append(semi)
  return modes

def get_notes(semis, key="C", accidental=""):
  accidental = accidental or key[1:] or "#"
  semi = notes_dict[key]
  notes = {}
  l_ind = letters.index(key[0].upper())
  letters2 = letters[l_ind:]+letters[:l_ind]
  intervals2 = intervals[l_ind:]+intervals[:l_ind]
  semi = semi%12
  # Build the scale notes dictionary
  for i, letter in zip(intervals2, letters2):
    # Find the right enharmonic spelling for this scale degree
    for note in semitones_dict[semi]:
      if note==letter or note[1:]==accidental:
        notes[semi] = note
        break
    semi = (semi+i)%12

  print(notes)

  # Generate output for the given semitones
  result = []
  for semi in semis:
    octave = (semi // 12) + 4
    semi_mod = semi % 12
    result.append(notes[semi_mod] + str(octave))
  return " ".join(result)

def change_key(semis, key1, mode1, key2, mode2):
  # Build the source scale (key1, mode1)
  semi = key1
  source_scale = []
  for i in intervals[mode1:]+intervals[:mode1]:  # Use standard intervals
    source_scale.append(semi%12)
    semi += i
  
  #3, 5, 7, 8, 10, 0, 2
  
  print(source_scale)

  
  # Build the target scale (key2, mode2)
  semi = key2
  target_scale = []
  for i in intervals[mode2:]+intervals[:mode2]:  # Use standard intervals
    target_scale.append(semi%12)
    semi += i

  # Convert each semitone
  semis2 = []
  for semi in semis:
    semi_mod = semi % 12
    octave_offset = semi // 12 * 12
    
    # Find which scale degree this note is in the source scale
    if semi_mod in source_scale:
      scale_degree = source_scale.index(semi_mod)
      # Map to the same scale degree in the target scale
      new_semi_mod = target_scale[scale_degree]
      semis2.append(octave_offset + new_semi_mod)
    
  return semis2


print(change_key(get_semitones(("O4", "C", "D", "E", "F", "G", "O5", "A", "B")), 3, 0, 3, 1))
      
#print(get_notes(change_key(get_semitones(("O4", "C", "D", "E", "F", "G", "O5", "A", "B")), 3, 0, 3, 1), "C", "b"))
