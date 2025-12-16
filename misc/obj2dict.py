def obj2dict(obj):
  d = {}
  d["__class__"] = type(obj).__name__
  d["__type__"] = None
  d["__attrs__"] = {}
  if isinstance(obj, dict):
    d["__type__"] = "dict"
    d["__value__"] = dict((obj2dict(key), obj2dict(value)) for key, value in obj.items())
  elif isinstance(obj, set):
    d["__type__"] = "set"
    d["__value__"] = [obj2dict(e) for e in obj]
  elif isinstance(obj, list):
    d["__type__"] = "list"
    d["__value__"] = [obj2dict(e) for e in obj]
  elif isinstance(obj, tuple):
    d["__type__"] = "tuple"
    d["__value__"] = [obj2dict(e) for e in obj]
  elif callable(obj):
    d["__type__"] = "func"
    d["__value__"] = None
  else:
    d["__value__"] = obj
  d["__attrs__"] = dict((attr, obj2dict(getattr(obj, attr))) for attr in dir(obj) if not (attr.startswith("__") and attr.endswith("__")))
  return d

class Dummy: 
  pass

def dict2obj(dct, globals_):
  a = Dummy()
  a.__class__ = globals_[dct["__class__"]]
  if dct["__type__"] == "dict":
    a.update((dict2obj(key), dict2obj(value)) for key, value in dct["__value__"])
  elif dct["__type__"] == "set":
    a.update(dict2obj(e) for e in dct["__value__"])
  elif dct["__type__"] == "list":
    a[:] = [(dict2obj(e) for e in dct["__value__"])]
  elif dct["__type__"] == "tuple":

