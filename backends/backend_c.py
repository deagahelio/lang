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
            else: # node.data == statement
                self.compiled += self.generate_statement(node)
    
    def generate_include(self, ast):
        if ast.data != "include":
            raise CompilerBackendException("invalid include type: " + ast.data)
        
        return f"#include " + ast.children[0].value[:-1] + ".h\"\n"
    
    def generate_function(self, ast):
        if ast.data not in ("function_typed", "function_void"):
            raise CompilerBackendException("invalid function type: " + ast.data)

        fn_name = ast.children[0].children[0].value

        if ast.data == "function_typed":
            fn_type = TYPE_MAP[ast.children[2].children[0].data]
        else: # ast.data == function_void
            fn_type = "void"

        self.context["current_function"] = fn_name
        
        return f"{fn_type} {fn_name}{self.generate_parameter_list(ast.children[1])}{{{self.generate_block(ast.children[-1])}}}"
    
    def generate_parameter_list(self, ast):
        if ast.data != "parameter_list":
            raise CompilerBackendException("invalid parameter list type: " + ast.data)

        if len(ast.children) != 0:
            return "(" + ','.join(TYPE_MAP[node.children[0].children[0].data] + " " + node.children[1].children[0].value for node in ast.children) + ")"
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
            return f"for(size_t {ast.children[0].children[0].value}={ast.children[1].children[0].children[0].value};\
{ast.children[0].children[0].value}<{ast.children[1].children[1].children[0].value};{ast.children[0].children[0].value}++){{{self.generate_block(ast.children[2])}}}"
        else:
            raise CompilerBackendException("invalid statement type: " + ast.data)
            
    def generate_expression(self, ast):
        if ast.data == "expression_function_call":
            fn_name = ast.children[0].children[0].value
            if fn_name == "this":
                fn_name = self.context["current_function"]
            fn_argument_list = self.generate_argument_list(ast.children[1])
            return f"{fn_name}{fn_argument_list}"
        elif ast.data == "expression_op_bin":
            return f"({self.generate_expression(ast.children[0])}{OP_BIN_MAP[ast.children[1].data]}{self.generate_expression(ast.children[2])})"
        elif ast.data == "expression_value":
            return f"({ast.children[0].children[0].value})"
        else:
            raise CompilerBackendException("invalid expression type: " + ast.data)
    
    def generate_argument_list(self, ast):
        if ast.data != "argument_list":
            raise CompilerBackendException("invalid argument list type: " + ast.data)
        
        return f"({','.join(map(self.generate_expression, ast.children))})"
    
    def write_output(self):
        source_file = self.output + ".source.c"

        with open(source_file, "w") as f:
            f.write(self.compiled)

        subprocess.run([self.compiler, source_file, "-o", self.output] + self.flags)

        if not self.args.keep_intermediate:
            os.remove(source_file)