from typing import List, Dict

class Type:
    def __init__(self, type: str, name: str, ptr: int):
        self.type = type
        self.name = name
        self.ptr = ptr

    def __eq__(self, other):
        return (
            self.type == other.type and
            self.name == other.name and
            self.ptr == other.ptr)

    def __str__(self, i=0):
        return i * "\t" + f"{self.name}{self.ptr * '*'}"

    def copy(self):
        return Type(self.type, self.name, self.ptr)

    def str(self, i=0):
        return self.__str__(i)

class Param:
    def __init__(self, type: Type, name: str):
        self.type = type
        self.name = name

    def __eq__(self, other):
        return (
            self.type == other.type and
            self.name == other.name)

    def __str__(self, i=0):
        return i * "\t" + f"{str(self.type)} {self.name}"
    
    def copy(self):
        return Param(self.type, self.name)
    
    def str(self, i=0):
        return self.__str__(i)

class Func:
    def __init__(self, type: Type, params: List[Param]):
        self.type = type
        self.params = params

    def __eq__(self, other):
        return (
            self.type == other.type and
            self.params == other.params)

    def __str__(self, name="", i=0):
        return """\
{i}fn {type} {name}({params})\
""".format(
    type=self.type if self.type != None else "\b",
    name=name,
    params=", ".join(param.str() for param in self.params),
    i=i * "\t")

    def copy(self):
        return Func(self.type, self.params)

    def str(self, name="", i=0):
        return self.__str__(name, i)

class Struct:
    def __init__(self, vars: Dict[str, Type] = {}, fns: Dict[str, Func] = {}, overloads: Dict[str, Func] = {}):
        self.vars = vars
        self.fns = fns
        self.overloads = overloads

    def __eq__(self, other):
        return (
            self.vars == other.vars and
            self.fns == other.fns and
            self.overloads == other.overloads)

    def __str__(self, name="", i=0):
        return """\
{i}struct {name} {{
{vars}
{fns}
{overloads}
{i}}}\
""".format(
    name=name,
    vars="\n".join("\t" * i + f"\tvar {v.str()} {k}" for k, v in self.vars.items()),
    fns="\n".join(v.str(k, i + 1) for k, v in self.fns.items()),
    overloads="\n".join(v.str(k, i + 1) for k, v in self.overloads.items()),
    i=i * "\t")

    def copy(self):
        return Struct(self.vars, self.fns, self.overloads)

    def str(self, name="", i=0):
        return self.__str__(name, i)

class Export:
    def __init__(self, structs: Dict[str, Struct] = {}, fns: Dict[str, Func] = {}, vars: Dict[str, Type] = {}):
        self.structs = structs
        self.fns = fns
        self.vars = vars

    def __eq__(self, other):
        return (
            self.structs == other.structs and
            self.fns == other.fns and
            self.vars == other.vars)

    def __str__(self, i=0):
        return """\
{i}export {{
{i}\tstructs {{
{structs}
{i}\t}}
{i}\tfns {{
{fns}
{i}\t}}
{i}\tvars {{
{vars}
{i}\t}}
{i}}}\
""".format(
    structs="\n".join(v.str(k, i + 2) for k, v in self.structs.items()),
    fns="\n".join(v.str(k, i + 2) for k, v in self.fns.items()),
    vars="\n".join(v.str(k, i + 2) for k, v in self.vars.items()),
    i=i * "\t")

    def copy(self):
        return Export(self.structs, self.fns, self.vars)

    def str(self, i=0):
        return self.__str__(i)