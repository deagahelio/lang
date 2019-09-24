import subprocess, os

from .exceptions import CompilerBackendException
from .types import Type, Param, Func, Struct, Export
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
    "uptr": "uintptr_t",
    "i8": "int8_t",
    "i16": "int16_t",
    "i32": "int32_t",
    "i64": "int64_t",
    "iptr": "intptr_t",
    "f32": "float",
    "f64": "double",
    "bool": "bool",
}

VALUE_TYPE_MAP = {
    "number": Type("builtin", "int", 0),
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
INT_TYPES = list(TYPE_MAP.keys())[:10]

class CBackend(BaseBackend):
    def __init__(self, args):
        super().__init__(args)
        self.compiler = "gcc" # TODO: make this take from args
        self.flags = "-Wextra -Wall -Wfloat-equal -Wpointer-arith -Wstrict-prototypes -Wwrite-strings -Wunreachable-code -O3".split(" ") # TODO: this too
        self.compiled = ""
        self.context = {}
        self.export = None

    def push_locals(self):
        self.context["locals_stack"].append({})
        self.locals = self.context["locals_stack"][-1]
    
    def pop_locals(self):
        if len(self.context["locals_stack"]) == 1:
            self.locals = None
        else:
            self.locals = self.context["locals_stack"][-2]
        return self.context["locals_stack"].pop()
    
    def generate(self, ast):
        if ast.data != "program":
            raise CompilerBackendException("invalid program type: " + ast.data)

        self.compiled = ""
        self.context = {
            "includes": "", # Header includes for the C code
            "data_decls": "", # Forward declarations for the C code
            "fn_decls": "",
            "locals_stack": [], # This will be initialized later in generate_function
            "later": {},
        }
        self.locals = None
        self.export = Export()

        for node in ast.children:
            if node.data == "include":
                self.context["includes"] = self.context["includes"] + self.generate_include(node)
            elif node.data in ("function_typed", "function_void"):
                self.compiled += self.generate_function(node)
            elif node.data == "struct":
                self.compiled += self.generate_struct(node)
            else: # node.data == statement
                self.compiled += self.generate_statement(node)
        
        self.compiled = BASE_CODE + self.context["includes"] + self.context["data_decls"] + self.context["fn_decls"] + self.compiled
    
    def generate_struct(self, ast):
        if ast.data != "struct":
            raise CompilerBackendException("invalid struct type: " + ast.data)

        compiled = ""

        # Set struct name for generate_struct_block
        struct_name = ast.children[0].children[0].value
        self.context["struct_name"] = struct_name
        self.export.structs[struct_name] = Struct()

        struct_block = self.generate_struct_block(ast.children[1])

        # We put the generated struct in data_decls
        # compiled contains only the generated methods
        self.context["data_decls"] += f"struct {struct_name}{{{struct_block}}};"

        struct_init_block = "" # Block for the struct's init function

        # context.later.methods was set by generate_struct_block
        # Since it doesn't generate the methods, we do that now
        for name, node in self.context["later"]["methods"].items():
            # Set function pointer in struct to the method we'll generate after
            struct_init_block += f"self->{name}=&__struct_{struct_name}_{name};"
            # Generate the method
            compiled += self.generate_function(node, method=True)

        # Create the declaration for the struct's init function
        init_declaration = f"void __struct_{struct_name}_init(struct {struct_name}* self)"
        self.context["fn_decls"] += init_declaration + ";"

        # Add the init function after every other method so that it can reference them
        # As stated earlier, compiled only includes the generated methods,
        # since the struct is in data_decls
        compiled += f"{init_declaration}{{{struct_init_block}}}"

        return compiled
    
    def generate_struct_block(self, ast):
        if ast.data != "struct_block":
            raise CompilerBackendException("invalid struct block type: " + ast.data)

        compiled = ""

        # We just need to know the declaration of the methods to generate the function pointers,
        # so we won't generate the methods yet
        self.context["later"]["methods"] = {}

        for node in ast.children:
            if node.data == "struct_property":
                var_type = self.generate_type(node.children[1])
                var_name = node.children[0].children[0].value

                # Export the struct property
                self.export.structs[self.context["struct_name"]].vars[var_name] = self.parse_type(node.children[1])

                compiled += f"{var_type} {var_name};"

            else: # node.data == function
                fn_name = node.children[0].children[0].value
                fn_params = self.generate_parameter_list(node.children[1], method=True)
                fn_type = "void" if node.data == "function_void" else self.generate_type(node.children[-2])

                compiled += f"{fn_type} (*{fn_name}){fn_params};"

                # Generate the method later
                self.context["later"]["methods"][fn_name] = node

                # The method will be exported later when generate_function gets called
        
        return compiled

    def generate_include(self, ast):
        if ast.data != "include":
            raise CompilerBackendException("invalid include type: " + ast.data)
        
        include_string = ast.children[0].value

        return f"#include {include_string[:-1]}.h\"\n"
    
    def generate_function(self, ast, method=False):
        if ast.data not in ("function_typed", "function_void"):
            raise CompilerBackendException("invalid function type: " + ast.data)

        self.push_locals()

        if method:
            # Raw method name (ex. bar)
            pure_fn_name = ast.children[0].children[0].value

            # Mangled method name (ex. __struct_Foo_bar)
            struct_name = self.context["struct_name"]
            fn_name = f"__struct_{struct_name}_{pure_fn_name}"
        else:
            # No mangling if it's a global function,
            # so set both variables to the same value
            pure_fn_name = ast.children[0].children[0].value
            fn_name = pure_fn_name

        if ast.data == "function_typed":
            fn_type = self.generate_type(ast.children[2])
        else: # ast.data == function_void
            fn_type = "void"

        fn_params = self.generate_parameter_list(ast.children[1], method=method)       

        export_type = self.parse_type(ast.children[2]) if fn_type != "void" else None
        export_params = [Param(self.parse_type(node.children[1]), node.children[0].children[0].value) for node in ast.children[1].children]

        # Set this for generate_expression
        self.context["current_function"] = fn_name
        self.context["current_return_type"] = export_type

        fn_declaration = f"{fn_type} {fn_name}{fn_params}"

        self.context["fn_decls"] += fn_declaration + ";"

        if method:
            export = self.export.structs[struct_name].fns
        else:
            export = self.export.fns

        export[pure_fn_name] = Func(export_type, export_params)

        fn_block = self.generate_block(ast.children[-1])

        self.pop_locals()

        return f"{fn_declaration}{{{fn_block}}}"
    
    def generate_parameter_list(self, ast, method=False):
        if ast.data != "parameter_list":
            raise CompilerBackendException("invalid parameter list type: " + ast.data)

        if len(ast.children) != 0:
            params = []

            for node in ast.children:
                var_name = node.children[0].children[0].value
                var_type = self.generate_type(node.children[1])

                params.append(f"{var_type} {var_name}")

                # Register parameter as a local variable so it can be referenced later in the function
                if self.locals != None:
                    self.locals[var_name] = self.parse_type(node.children[1])

            inner = ','.join(params)

            if method:
                struct_name = self.context["struct_name"]

                # Register self as a local variable
                if self.locals != None:
                    self.locals["self"] = Type("struct", struct_name, 1)
                # Add self as first parameter
                inner = f"struct {struct_name}* self," + inner

            return f"({inner})"
        else:
            if method:
                struct_name = self.context["method_data"]["struct_name"]

                return f"(struct {struct_name}* self)"
            else:
                # () as parameter list in C means function accepts anything as argument,
                # so we use (void) instead to not accept arguments
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
            expr = self.generate_expression(ast.children[0])
            return f"{expr};"

        elif ast.data == "statement_return":
            expr = self.generate_expression(ast.children[0])
            return f"return {expr};"

        elif ast.data == "statement_if":
            if_expr = self.generate_expression(ast.children[0])
            if_block = self.generate_block(ast.children[1])

            compiled = f"if({if_expr}){{{if_block}}}"
            
            # If statement has else/elif blocks
            if len(ast.children) > 2:
                # Loop over each elif expression
                #
                # If statement has this grammar:
                #   "if" expression block ("elif" expression block)* ["else" block]
                # Strings get discarded after parsing, so they can be ignored
                #
                # Range starts at first elif expression (third element),
                # ends at last elif block (second to last element)
                # and skips one element every step (the current elif block)
                for i in range(2, len(ast.children) - 1, 2):
                    elif_expr = self.generate_expression(ast.children[i])
                    elif_block = self.generate_block(ast.children[i + 1])

                    compiled += f"else if({elif_expr}){{{elif_block}}}"

                else_block = self.generate_block(ast.children[-1])

                compiled += f"else{{{else_block}}}"

            return compiled

        elif ast.data == "statement_for":
            for_var = ast.children[0].children[0].value
            for_start = ast.children[1].children[0].children[0].value
            for_end = ast.children[1].children[1].children[0].value

            # Register loop variable as local
            self.locals[for_var] = Type("builtin", "uptr", 0)

            for_block = self.generate_block(ast.children[2])

            return f"for(uintptr_t {for_var}={for_start};{for_var}<{for_end};{for_var}++){{{for_block}}}"

        elif ast.data == "statement_variable_define_auto":
            # Infer variable type from expression
            expr_type = self.infer_type(ast.children[1])

            # infer_type returns a type of name "int" for integer literals,
            # so make sure to set it to the appropriate type before we register it
            if expr_type.type == "builtin" and expr_type.name == "int":
                expr_type.name = "i32"

            var_type = self.generate_parsed_type(expr_type)
            var_name = ast.children[0].children[0].value
            var_expr = self.generate_expression(ast.children[1])

            # Register local variable
            self.locals[var_name] = expr_type

            return f"{var_type} {var_name}={var_expr};"

        elif ast.data == "statement_variable_define":
            var_type = self.generate_type(ast.children[1])
            var_name = ast.children[0].children[0].value
            var_expr = self.generate_expression(ast.children[2])

            # Register local variable
            self.locals[var_name] = self.parse_type(ast.children[1])

            return f"{var_type} {var_name}={var_expr};"

        elif ast.data == "statement_variable_declare":
            var_type = self.generate_type(ast.children[1])
            var_name = ast.children[0].children[0].value

            # Register local variable
            self.locals[var_name] = self.parse_type(ast.children[1])

            if self.locals[var_name].type == "struct" and self.locals[var_name].ptr == 0:
                struct_name = ast.children[1].children[0].children[0].value
                compiled = f"{var_type} {var_name}={{0}};__struct_{struct_name}_init(&{var_name});"
            else:
                compiled = f"{var_type} {var_name};"

            return compiled

        elif ast.data == "statement_variable_assign":
            expr = self.generate_expression(ast.children[0])
            expr_new = self.generate_expression(ast.children[1])

            return f"{expr}={expr_new};"

        else:
            raise CompilerBackendException("invalid statement type: " + ast.data)
            
    def generate_expression(self, ast):
        if ast.data == "expression_ref":
            expr = self.generate_expression(ast.children[0])
            return f"(&{expr})"

        elif ast.data == "expression_deref":
            expr = self.generate_expression(ast.children[0])
            return f"(*{expr})"

        elif ast.data == "expression_function_call":
            # TODO: change this back to expression someday
            fn_name = ast.children[0].children[0].value
            # TODO: make this handle methods (if it doesn't already)
            if fn_name == "this":
                fn_name = self.context["current_function"]

            if ast.children[0].data in ("expression_dot", "expression_arrow"):
                method = True
                # Set struct name for generate_argument_list
                op = "&" if ast.children[0].data == "expression_dot" else ""
                self.context["struct_name"] = op + self.generate_expression(ast.children[0].children[0])
            else:
                method = False

            fn_args = self.generate_argument_list(ast.children[1], method=method)

            return f"({fn_name}{fn_args})"

        elif ast.data == "expression_op_bin":
            expr_l = self.generate_expression(ast.children[0])
            expr_r = self.generate_expression(ast.children[2])
            type_l = self.infer_type(ast.children[0])
            type_r = self.infer_type(ast.children[2])

            if type_l == type_r:
                if type_l.type == "struct" and type_l.ptr == 0:
                    # Check if struct implemented overloading for operator
                    fn_name = f"__{ast.children[1].data}__"
                    if fn_name in self.export.structs[type_l.name].fns:
                        return f"(__struct_{type_l.name}_{fn_name}(&{expr_l},&{expr_r}))"
            # Check if left has defined int type and right is a literal
            elif ((type_l.type == "builtin" and
                   type_l.name in INT_TYPES and
                   type_l.ptr == 0 and
                   type_r == VALUE_TYPE_MAP["number"]) or
            # Check if right has defined int type and left is a literal
                  (type_r.type == "builtin" and
                   type_r.name in INT_TYPES and
                   type_r.ptr == 0 and
                   type_l == VALUE_TYPE_MAP["number"])):
                pass
            else:
                raise CompilerBackendException(f"can't apply binary operation to expressions of different type: {type_l} and {type_r}")

            # Generate expression
            expr_op = OP_BIN_MAP[ast.children[1].data]
            return f"({expr_l}{expr_op}{expr_r})"

        elif ast.data == "expression_dot":
            expr = self.generate_expression(ast.children[0])
            expr_type = self.infer_type(ast.children[0])
            name = ast.children[1].children[0].value

            if expr_type.type == "struct":
                if expr_type.ptr == 0:
                    compiled = f"({expr}.{name})"
                elif expr_type.ptr == 1:
                    compiled = f"({expr}->{name})"
                else:
                    raise CompilerBackendException("can't use dot operator with multiple pointer layers")
            else:
                raise CompilerBackendException("left side of dot expression is not struct or struct pointer")

            return compiled

        elif ast.data == "expression_value":
            value = ast.children[0].children[0].value
            return f"({value})"

        else:
            raise CompilerBackendException("invalid expression type: " + ast.data)
    
    def generate_argument_list(self, ast, method=False):
        if ast.data != "argument_list":
            raise CompilerBackendException("invalid argument list type: " + ast.data)
        
        inner = ",".join(map(self.generate_expression, ast.children))
        
        if method:
            inner = self.context["struct_name"] + "," + inner

        return f"({inner})"
    
    def generate_type(self, ast):
        # Generate pointer asterisks
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
        # Count pointer layers
        ptr = 0
        try:
            if ast.children[-1].value[0] == "*":
                ptr = len(ast.children[-1].value)
        except AttributeError:
            pass

        if ast.data == "type_builtin":
            return Type("builtin", ast.children[0].data, ptr)
        elif ast.data == "type_userdef":
            return Type("struct", ast.children[0].children[0].value, ptr)
        else:
            raise CompilerBackendException("can't parse unknown type type: " + ast.data)
    
    def generate_parsed_type(self, type):
        ptr = "*" * type.ptr

        if type.type == "builtin":
            return TYPE_MAP[type.name] + ptr
        elif type.type == "struct":
            return "struct " + type.name + ptr
        else:
            raise CompilerBackendException("invalid type type: " + type.type)

    def infer_type(self, ast):
        if ast.data == "expression_ref":
            # Increment pointer count
            type = self.infer_type(ast.children[0])
            return type._replace(ptr=type.ptr + 1)

        elif ast.data == "expression_deref":
            # Decrement pointer count
            type = self.infer_type(ast.children[0])
            return type._replace(ptr=type.ptr - 1)

        elif ast.data == "expression_function_call":
            fn_name = ast.children[0].children[0].value

            if fn_name == "this":
                return self.context["current_return_type"]
            if fn_name in self.export.fns:
                return self.export.fns[fn_name].type
            else:
                raise CompilerBackendException("function doesn't exist: " + fn_name)

        elif ast.data == "expression_op_bin":
            type_l = self.infer_type(ast.children[0])
            type_r = self.infer_type(ast.children[2])

            if (type_l == type_r or
                (type_l.type == "builtin" and
                 type_l.name in INT_TYPES and
                 type_l.ptr == 0 and
                 type_r == VALUE_TYPE_MAP["number"])):
                return type_l
            elif (type_r.type == "builtin" and
                  type_r.name in INT_TYPES and
                  type_r.ptr == 0 and
                  type_l == VALUE_TYPE_MAP["number"]):
                return type_r
            else:
                raise CompilerBackendException(f"can't apply binary operation to expressions of different type: {type_l} and {type_r}")

        elif ast.data == "expression_dot":
            type_l = self.infer_type(ast.children[0])
            name = ast.children[1].children[0].value

            if not type_l.type == "struct":
                raise CompilerBackendException("left side of dot expression is not struct or struct pointer")

            return self.export.structs[type_l.name].vars[name]

        elif ast.data == "expression_value":
            type = ast.children[0].data

            if type == "ident":
                return self.locals[ast.children[0].children[0].value]

            # Need to make a copy otherwise it returns a reference to the object
            return VALUE_TYPE_MAP[type].copy()

        elif ast.data == "ident":
            return self.locals[ast.children[0].value]

        else:
            raise CompilerBackendException("don't know how to infer unknown expression type: " + ast.data)
    
    def write_output(self):
        source_file = self.output + ".source.c"

        with open(source_file, "w") as f:
            f.write(self.compiled)

        subprocess.run([self.compiler, source_file, "-o", self.output] + self.flags)

        if not self.args.keep_intermediate:
            os.remove(source_file)