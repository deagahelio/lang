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

        self.compiled = BASE_CODE
        self.context = {}

        for node in ast.children:
            if node.data == "include":
                self.compiled += self.generate_include(node)
            elif node.data in ("function_typed", "function_void"):
                self.compiled += self.generate_function(node)
            elif node.data == "struct":
                self.compiled += self.generate_struct(node)
            else: # node.data == statement
                self.compiled += self.generate_statement(node)
    
    def generate_struct(self, ast):
        struct_name = ast.children[0].children[0].value

        self.context["method_data"] = {
            "struct_name": struct_name,
        }

        struct_block = self.generate_struct_block(ast.children[1])
        struct_init_block = ""

        compiled = f"struct {struct_name}{{{struct_block}}};"

        for name, node in self.context["later"]["methods"].items():
            struct_init_block += f"self->{name}=&__struct_{struct_name}_{name};"
            compiled += self.generate_function(node, method=True)

        compiled += f"void __struct_{struct_name}_init(struct {struct_name}* self){{{struct_init_block}}}"

        return compiled
    
    def generate_struct_block(self, ast):
        compiled = ""
        self.context["later"] = {
            "methods": {}
        }

        for node in ast.children:
            if node.data == "struct_property":
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
            fn_name = f"__struct_{self.context['method_data']['struct_name']}_{ast.children[0].children[0].value}"
        else:
            fn_name = ast.children[0].children[0].value

        if ast.data == "function_typed":
            fn_type = self.generate_type(ast.children[2])
        else: # ast.data == function_void
            fn_type = "void"

        self.context["current_function"] = fn_name
        
        return f"{fn_type} {fn_name}{self.generate_parameter_list(ast.children[1], method=method)}{{{self.generate_block(ast.children[-1])}}}"
    
    def generate_parameter_list(self, ast, method=False):
        if ast.data != "parameter_list":
            raise CompilerBackendException("invalid parameter list type: " + ast.data)

        if len(ast.children) != 0:
            inner = ','.join(self.generate_type(node.children[0]) + " " + node.children[1].children[0].value for node in ast.children)
            if method:
                inner = f"struct {self.context['method_data']['struct_name']}* self," + inner
            return f"({inner})"
        else:
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
            if ast.children[0].data == "type_struct":
                compiled = f"{self.generate_type(ast.children[0])} {ast.children[1].children[0].value}={{0}};\
__struct_{ast.children[0].children[0].children[0].value}_init(&{ast.children[1].children[0].value});"
            else:
                compiled = f"{self.generate_type(ast.children[0])} {ast.children[1].children[0].value};"
            return compiled
        else:
            raise CompilerBackendException("invalid statement type: " + ast.data)
            
    def generate_expression(self, ast):
        if ast.data == "expression_function_call":
            fn_name = self.generate_expression(ast.children[0])
            if fn_name == "this":
                fn_name = self.context["current_function"]

            if ast.children[0].data in ("expression_dot", "expression_arrow"):
                method = True
                op = "&" if ast.children[0].data == "expression_dot" else ""
                self.context["method_data"] = {
                    "struct_name": op + self.generate_expression(ast.children[0].children[0]),
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
            inner = self.context["method_data"]["struct_name"] + "," + inner

        return f"({inner})"
    
    def generate_type(self, ast):
        if ast.data == "type_builtin":
            return TYPE_MAP[ast.children[0].data]
        elif ast.data == "type_struct":
            return "struct " + ast.children[0].children[0].value
        else:
            raise CompilerBackendException("invalid type type: " + ast.data)
    
    def write_output(self):
        source_file = self.output + ".source.c"

        with open(source_file, "w") as f:
            f.write(self.compiled)

        subprocess.run([self.compiler, source_file, "-o", self.output] + self.flags)

        if not self.args.keep_intermediate:
            os.remove(source_file)