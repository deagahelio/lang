import subprocess, os

from .exceptions import CompilerBackendException
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
    
    def generate(self, ast):
        if ast.data != "program":
            raise CompilerBackendException("invalid program type: " + ast.data)

        self.compiled = ""
        self.context = {
            "includes": "",
            "data_decls": "",
            "fn_decls": "",
            "overloads": {},
        }

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

        self.context["method_data"] = {
            "class_name": class_name,
        }

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
                compiled += f"{self.generate_type(node.children[0])} {node.children[1].children[0].value};"
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

        if method:
            pure_fn_name = ast.children[0].children[0].value
            class_name = self.context['method_data']['class_name']
            fn_name = f"__class_{class_name}_{pure_fn_name}"
            if pure_fn_name in OVERLOAD_NAMES:
                if class_name not in self.context["overloads"]:
                    self.context["overloads"][class_name] = []
                self.context["overloads"][class_name].append(pure_fn_name)
        else:
            fn_name = ast.children[0].children[0].value

        if ast.data == "function_typed":
            fn_type = self.generate_type(ast.children[2])
        else: # ast.data == function_void
            fn_type = "void"

        self.context["current_function"] = fn_name
        
        fn_declaration = f"{fn_type} {fn_name}{self.generate_parameter_list(ast.children[1], method=method)}"

        self.context["fn_decls"] += fn_declaration + ";"

        return f"{fn_declaration}{{{self.generate_block(ast.children[-1])}}}"
    
    def generate_parameter_list(self, ast, method=False):
        if ast.data != "parameter_list":
            raise CompilerBackendException("invalid parameter list type: " + ast.data)

        if len(ast.children) != 0:
            inner = ','.join(self.generate_type(node.children[0]) + " " + node.children[1].children[0].value for node in ast.children)
            if method:
                inner = f"struct {self.context['method_data']['class_name']}* self," + inner
            return f"({inner})"
        else:
            if method:
                return f"(struct {self.context['method_data']['class_name']}* self)"
            return "(void)"

    def generate_block(self, ast):
        if ast.data != "block":
            raise CompilerBackendException("invalid block type: " + ast.data)

        compiled = ""

        for node in ast.children:
            compiled += self.generate_statement(node)
        
        return compiled
    
    def generate_statement(self, ast):
        if ast.data == "statement":
            return f"{self.generate_expression(ast.children[0])};"
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
            return f"{self.generate_type(ast.children[0])} {ast.children[1].children[0].value}={self.generate_expression(ast.children[2])};"
        elif ast.data == "statement_variable_declare":
            if ast.children[0].data == "type_class":
                compiled = f"{self.generate_type(ast.children[0])} {ast.children[1].children[0].value}={{0}};\
__class_{ast.children[0].children[1].children[0].value}_init(&{ast.children[1].children[0].value});"
            else:
                compiled = f"{self.generate_type(ast.children[0])} {ast.children[1].children[0].value};"
            return compiled
        elif ast.data == "statement_variable_assign":
            return f"{self.generate_expression(ast.children[0])}={self.generate_expression(ast.children[1])};"
        else:
            raise CompilerBackendException("invalid statement type: " + ast.data)
            
    def generate_expression(self, ast):
        if ast.data == "expression_ref":
            return f"(&{self.generate_expression(ast.children[0])})"
        elif ast.data == "expression_deref":
            return f"(*{self.generate_expression(ast.children[0])})"
        elif ast.data == "expression_function_call":
            fn_name = self.generate_expression(ast.children[0])
            if fn_name == "this":
                fn_name = self.context["current_function"]

            if ast.children[0].data in ("expression_dot", "expression_arrow"):
                method = True
                op = "&" if ast.children[0].data == "expression_dot" else ""
                self.context["method_data"] = {
                    "class_name": op + self.generate_expression(ast.children[0].children[0]),
                }
            else:
                method = False

            fn_argument_list = self.generate_argument_list(ast.children[1], method=method)
            return f"({fn_name}{fn_argument_list})"
        elif ast.data == "expression_op_bin":
            return f"({self.generate_expression(ast.children[0])}{OP_BIN_MAP[ast.children[1].data]}{self.generate_expression(ast.children[2])})"
        elif ast.data == "expression_arrow":
            return f"({self.generate_expression(ast.children[0])}->{ast.children[1].children[0].value})"
        elif ast.data == "expression_dot":
            return f"({self.generate_expression(ast.children[0])}.{ast.children[1].children[0].value})"
        elif ast.data == "expression_value":
            return f"({ast.children[0].children[0].value})"
        else:
            raise CompilerBackendException("invalid expression type: " + ast.data)
    
    def generate_argument_list(self, ast, method=False):
        if ast.data != "argument_list":
            raise CompilerBackendException("invalid argument list type: " + ast.data)
        
        inner = ','.join(map(self.generate_expression, ast.children))
        
        if method:
            inner = self.context["method_data"]["class_name"] + "," + inner

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
        elif ast.data == "type_class":
            return "struct " + ast.children[1].children[0].value + ptr
        else:
            raise CompilerBackendException("invalid type type: " + ast.data)
    
    def write_output(self):
        source_file = self.output + ".source.c"

        with open(source_file, "w") as f:
            f.write(self.compiled)

        subprocess.run([self.compiler, source_file, "-o", self.output] + self.flags)

        if not self.args.keep_intermediate:
            os.remove(source_file)