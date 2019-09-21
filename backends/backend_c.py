import subprocess, os

from .exceptions import CompilerBackendException
from .types import Type, Param, Func, Class, Export
from .backend_base import BaseBackend

BASE_CODE = """\
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
"""

TYPE_MAP = {
    "u8": "uint8_t",
    "u16": "uint16_t",
    "u32": "uint32_t",
    "u64": "uint64_t",
    "i8": "int8_t",
    "i16": "int16_t",
    "i32": "int32_t",
    "i64": "int64_t",
    "f32": "float",
    "f64": "double",
    "bool": "bool",
}

VALUE_TYPE_MAP = {
    "number": Type("builtin", "i32", 0),
    "string": Type("builtin", "u8", 1),
    "true": Type("builtin", "bool", 0),
    "false": Type("builtin", "bool", 0),
}

OP_BIN_MAP = {
    "add": "+",
    "subtract": "-",
    "multiply": "*",
    "divide": "/",
    "equal": "==",
    "not_equal": "!=",
    "less_than": "<",
    "greater_than": ">",
    "add_eq": "+=",
    "subtract_eq": "-=",
    "multiply_eq": "*=",
    "divide_eq": "/=",
}

OVERLOAD_NAMES = [f"__{op}__" for op in OP_BIN_MAP.keys()]

class CBackend(BaseBackend):
    def __init__(self, args):
        super().__init__(args)
        self.compiler = "gcc" # TODO: make this take from args
        self.flags = "-Wextra -Wall -Wfloat-equal -Wpointer-arith -Wstrict-prototypes -Wwrite-strings -Wunreachable-code -O3".split(" ") # TODO: this too
        self.compiled = ""
        self.context = {}
        self.export = None
    
    def generate(self, ast):
        if ast.data != "program":
            raise CompilerBackendException("invalid program type: " + ast.data)

        self.compiled = ""
        self.context = {
            "includes": "",
            "data_decls": "",
            "fn_decls": "",
            "overloads": {},
            "locals": [{}],
        }
        self.export = Export()

        for node in ast.children:
            if node.data == "include":
                self.context["includes"] = self.context["includes"] + self.generate_include(node)
            elif node.data in ("function_typed", "function_void"):
                self.compiled += self.generate_function(node)
            elif node.data == "class":
                self.compiled += self.generate_class(node)
            else: # node.data == statement
                self.compiled += self.generate_statement(node)
        
        self.compiled = BASE_CODE + self.context["includes"] + self.context["data_decls"] + self.context["fn_decls"] + self.compiled
    
    def generate_class(self, ast):
        class_name = ast.children[0].children[0].value

        self.context["class_name"] = class_name

        self.export.classes[class_name] = Class()

        class_block = self.generate_class_block(ast.children[1])
        class_init_block = ""

        self.context["data_decls"] += f"struct {class_name}{{{class_block}}};"

        compiled = ""

        for name, node in self.context["later"]["methods"].items():
            class_init_block += f"self->{name}=&__class_{class_name}_{name};"
            compiled += self.generate_function(node, method=True)

        init_declaration = f"void __class_{class_name}_init(struct {class_name}* self)"

        self.context["fn_decls"] += init_declaration + ";"

        compiled += f"{init_declaration}{{{class_init_block}}}"

        return compiled
    
    def generate_class_block(self, ast):
        compiled = ""
        self.context["later"] = {
            "methods": {}
        }

        for node in ast.children:
            if node.data == "class_property":
                var_name = node.children[1].children[0].value
                self.export.classes[self.context["class_name"]].vars[var_name] = self.parse_type(node.children[0])
                compiled += f"{self.generate_type(node.children[0])} {var_name};"
            else: # node.data == function
                if node.data == "function_void":
                    fn_type = "void"
                else: # node.data == function_typed
                    fn_type = self.generate_type(node.children[-2])
                fn_name = node.children[0].children[0].value

                compiled += f"{fn_type} (*{fn_name}){self.generate_parameter_list(node.children[1], method=True)};"
                self.context["later"]["methods"][fn_name] = node
        
        return compiled

    def generate_include(self, ast):
        if ast.data != "include":
            raise CompilerBackendException("invalid include type: " + ast.data)
        
        include_string = ast.children[0].value

        return f"#include " + include_string[:-1] + ".h\"\n"
    
    def generate_function(self, ast, method=False):
        if ast.data not in ("function_typed", "function_void"):
            raise CompilerBackendException("invalid function type: " + ast.data)

        locals = {}

        if method:
            pure_fn_name = ast.children[0].children[0].value
            class_name = self.context["class_name"]
            fn_name = f"__class_{class_name}_{pure_fn_name}"
            if pure_fn_name in OVERLOAD_NAMES:
                if class_name not in self.context["overloads"]:
                    self.context["overloads"][class_name] = []
                self.context["overloads"][class_name].append(pure_fn_name)
        else:
            fn_name = ast.children[0].children[0].value
            pure_fn_name = fn_name

        if ast.data == "function_typed":
            fn_type = self.generate_type(ast.children[2])
        else: # ast.data == function_void
            fn_type = "void"

        self.context["current_function"] = fn_name
        
        fn_declaration = f"{fn_type} {fn_name}{self.generate_parameter_list(ast.children[1], method=method, locals=locals)}"

        self.context["fn_decls"] += fn_declaration + ";"

        if method:
            export = self.export.classes[class_name].fns
        else:
            export = self.export.fns

        export_type = self.parse_type(ast.children[2]) if fn_type != "void" else None
        export_params = [Param(self.parse_type(node.children[0]), node.children[1].children[0].value) for node in ast.children[1].children]

        export[pure_fn_name] = Func(export_type, export_params)

        return f"{fn_declaration}{{{self.generate_block(ast.children[-1], locals=locals)}}}"
    
    def generate_parameter_list(self, ast, method=False, locals=None):
        if ast.data != "parameter_list":
            raise CompilerBackendException("invalid parameter list type: " + ast.data)

        if len(ast.children) != 0:
            params = []
            for node in ast.children:
                var_name = node.children[1].children[0].value
                params.append(self.generate_type(node.children[0]) + " " + var_name)
                if locals != None:
                    locals[var_name] = self.parse_type(node.children[0])
            inner = ','.join(params)
            if method:
                class_name = self.context["class_name"]
                if locals != None:
                    locals["self"] = Type("class", class_name, 1)
                inner = f"struct {class_name}* self," + inner
            return f"({inner})"
        else:
            if method:
                return f"(struct {self.context['method_data']['class_name']}* self)"
            return "(void)"

    def generate_block(self, ast, locals=None):
        if ast.data != "block":
            raise CompilerBackendException("invalid block type: " + ast.data)

        compiled = ""

        for node in ast.children:
            compiled += self.generate_statement(node, locals=locals)
        
        return compiled
    
    def generate_statement(self, ast, locals=None):
        if ast.data == "statement":
            return f"{self.generate_expression(ast.children[0], locals=locals)};"
        elif ast.data == "statement_return":
            return f"return {self.generate_expression(ast.children[0])};"
        elif ast.data == "statement_if":
            compiled = f"if({self.generate_expression(ast.children[0])}){{{self.generate_block(ast.children[1])}}}"
            
            if len(ast.children) > 2:
                for i in range(2, len(ast.children) - 1, 2):
                    compiled += f"else if({self.generate_expression(ast.children[i])}){{{self.generate_block(ast.children[i + 1])}}}"
                compiled += f"else{{{self.generate_block(ast.children[-1])}}}"

            return compiled
        elif ast.data == "statement_for":
            for_variable_name = ast.children[0].children[0].value
            for_start_value = ast.children[1].children[0].children[0].value
            for_end_value = ast.children[1].children[1].children[0].value
            return f"for(size_t {for_variable_name}={for_start_value};{for_variable_name}<{for_end_value};{for_variable_name}++){{{self.generate_block(ast.children[2])}}}"
        elif ast.data == "statement_variable_define":
            var_name = ast.children[1].children[0].value
            if locals != None:
                locals[var_name] = self.parse_type(ast.children[0])
            return f"{self.generate_type(ast.children[0])} {var_name}={self.generate_expression(ast.children[2], locals=locals)};"
        elif ast.data == "statement_variable_declare":
            var_name = ast.children[1].children[0].value
            if locals != None:
                locals[var_name] = self.parse_type(ast.children[0])
            if ast.children[0].data == "type_userdef":
                compiled = f"{self.generate_type(ast.children[0])} {var_name}={{0}};\
__class_{ast.children[0].children[0].children[0].value}_init(&{var_name});"
            else:
                compiled = f"{self.generate_type(ast.children[0])} {ast.children[1].children[0].value};"
            return compiled
        elif ast.data == "statement_variable_assign":
            return f"{self.generate_expression(ast.children[0], locals=locals)}={self.generate_expression(ast.children[1], locals=locals)};"
        else:
            raise CompilerBackendException("invalid statement type: " + ast.data)
            
    def generate_expression(self, ast, locals=None):
        if ast.data == "expression_ref":
            return f"(&{self.generate_expression(ast.children[0], locals=locals)})"
        elif ast.data == "expression_deref":
            return f"(*{self.generate_expression(ast.children[0], locals=locals)})"
        elif ast.data == "expression_function_call":
            fn_name = self.generate_expression(ast.children[0], locals=locals)
            if fn_name == "this":
                fn_name = self.context["current_function"]

            if ast.children[0].data in ("expression_dot", "expression_arrow"):
                method = True
                op = "&" if ast.children[0].data == "expression_dot" else ""
                self.context["class_name"] = op + self.generate_expression(ast.children[0].children[0], locals=locals)
            else:
                method = False

            fn_argument_list = self.generate_argument_list(ast.children[1], method=method)
            return f"({fn_name}{fn_argument_list})"
        elif ast.data == "expression_op_bin":
            type = self.infer_type(ast.children[0], locals=locals)
            if type.type == "class":
                fn_name = f"__{ast.children[1].data}__"
                if fn_name in self.export.classes[type.name].fns:
                    return f"(__class_{type.name}_{fn_name}(&{self.generate_expression(ast.children[0])},&{self.generate_expression(ast.children[2], locals=locals)}))"
            else:
                return f"({self.generate_expression(ast.children[0], locals=locals)}{OP_BIN_MAP[ast.children[1].data]}{self.generate_expression(ast.children[2], locals=locals)})"
        elif ast.data == "expression_arrow":
            return f"({self.generate_expression(ast.children[0], locals=locals)}->{ast.children[1].children[0].value})"
        elif ast.data == "expression_dot":
            return f"({self.generate_expression(ast.children[0], locals=locals)}.{ast.children[1].children[0].value})"
        elif ast.data == "expression_value":
            return f"({ast.children[0].children[0].value})"
        else:
            raise CompilerBackendException("invalid expression type: " + ast.data)
    
    def generate_argument_list(self, ast, method=False):
        if ast.data != "argument_list":
            raise CompilerBackendException("invalid argument list type: " + ast.data)
        
        inner = ','.join(map(self.generate_expression, ast.children))
        
        if method:
            inner = self.context["class_name"] + "," + inner

        return f"({inner})"
    
    def generate_type(self, ast):
        ptr = ""
        try:
            if ast.children[-1].value[0] == "*":
                ptr = ast.children[-1].value
        except AttributeError:
            pass

        if ast.data == "type_builtin":
            return TYPE_MAP[ast.children[0].data] + ptr
        elif ast.data == "type_userdef":
            return "struct " + ast.children[0].children[0].value + ptr
        else:
            raise CompilerBackendException("invalid type type: " + ast.data)
    
    def parse_type(self, ast):
        ptr = 0
        try:
            if ast.children[-1].value[0] == "*":
                ptr = len(ast.children[-1].value)
        except AttributeError:
            pass

        if ast.data == "type_builtin":
            return Type("builtin", ast.children[0].data, ptr)
        elif ast.data == "type_userdef":
            return Type("class", ast.children[0].children[0].value, ptr)
        else:
            raise CompilerBackendException("can't parse unknown type type: " + ast.data)

    def infer_type(self, ast, locals=None):
        if ast.data == "expression_ref":
            type = self.infer_type(ast.children[0], locals=locals)
            return type._replace(ptr=type.ptr + 1)
        elif ast.data == "expression_deref":
            type = self.infer_type(ast.children[0], locals=locals)
            return type._replace(ptr=type.ptr - 1)
        elif ast.data == "expression_function_call":
            raise CompilerBackendException("don't know how to infer function call type")
        elif ast.data == "expression_op_bin":
            type_l = self.infer_type(ast.children[0], locals=locals)
            type_r = self.infer_type(ast.children[2], locals=locals)
            if type_l == type_r:
                return type_l
            raise CompilerBackendException("can't apply binary operation to expressions of different type")
        elif ast.data == "expression_arrow":
            type_l = self.infer_type(ast.children[0], locals=locals)
            if not (type_l.type == "class" and type_l.ptr == 1):
                raise CompilerBackendException("left side of arrow expression is not pointer to class")
            return self.export.classes[type_l.name].vars[ast.children[1].children[0].value]
        elif ast.data == "expression_dot":
            raise CompilerBackendException("don't know how to infer dot expression type")
        elif ast.data == "expression_value":
            type = ast.children[0].data
            if type == "ident":
                return locals[ast.children[0].children[0].value]
            return VALUE_TYPE_MAP[type]
        elif ast.data == "ident":
            return locals[ast.children[0].value]
        else:
            raise CompilerBackendException("don't know how to infer unknown expression type: " + ast.data)
    
    def write_output(self):
        source_file = self.output + ".source.c"

        with open(source_file, "w") as f:
            f.write(self.compiled)

        subprocess.run([self.compiler, source_file, "-o", self.output] + self.flags)

        if not self.args.keep_intermediate:
            os.remove(source_file)