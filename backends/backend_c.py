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

    def push_locals(self):
        self.context["locals_stack"].append({})
        self.locals = self.context["locals_stack"][-1]
    
    def pop_locals(self):
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
        }
        self.locals = None
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
        if ast.data != "class":
            raise CompilerBackendException("invalid class type: " + ast.data)

        compiled = ""

        # Set class name for generate_class_block
        class_name = ast.children[0].children[0].value
        self.context["class_name"] = class_name
        self.export.classes[class_name] = Class()

        class_block = self.generate_class_block(ast.children[1])

        # We put the generated struct in data_decls
        # compiled contains only the generated methods
        self.context["data_decls"] += f"struct {class_name}{{{class_block}}};"

        class_init_block = "" # Block for the class's init function

        # context.later.methods was set by generate_class_block
        # Since it doesn't generate the methods, we do that now
        for name, node in self.context["later"]["methods"].items():
            # Set function pointer in struct to the method we'll generate after
            class_init_block += f"self->{name}=&__class_{class_name}_{name};"
            # Generate the method
            compiled += self.generate_function(node, method=True)

        # Create the declaration for the class's init function
        init_declaration = f"void __class_{class_name}_init(struct {class_name}* self)"
        self.context["fn_decls"] += init_declaration + ";"

        # Add the init function after every other method so that it can reference them
        # As stated earlier, compiled only includes the generated methods,
        # since the struct is in data_decls
        compiled += f"{init_declaration}{{{class_init_block}}}"

        return compiled
    
    def generate_class_block(self, ast):
        if ast.data != "class_block":
            raise CompilerBackendException("invalid class block type: " + ast.data)

        compiled = ""

        # We just need to know the declaration of the methods to generate the function pointers,
        # so we won't generate the methods yet
        self.context["later"] = {
            "methods": {}
        }

        for node in ast.children:
            if node.data == "class_property":
                var_type = self.generate_type(node.children[0])
                var_name = node.children[1].children[0].value

                # Export the class property
                self.export.classes[self.context["class_name"]].vars[var_name] = self.parse_type(node.children[0])

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

            # Mangled method name (ex. __class_Foo_bar)
            class_name = self.context["class_name"]
            fn_name = f"__class_{class_name}_{pure_fn_name}"
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

        # Set this for generate_expression
        self.context["current_function"] = fn_name

        fn_declaration = f"{fn_type} {fn_name}{fn_params}"

        self.context["fn_decls"] += fn_declaration + ";"

        if method:
            export = self.export.classes[class_name].fns
        else:
            export = self.export.fns

        export_type = self.parse_type(ast.children[2]) if fn_type != "void" else None
        export_params = [Param(self.parse_type(node.children[0]), node.children[1].children[0].value) for node in ast.children[1].children]

        export[pure_fn_name] = Func(export_type, export_params)

        fn_block = self.generate_block(ast.children[-1])

        return f"{fn_declaration}{{{fn_block}}}"
    
    def generate_parameter_list(self, ast, method=False):
        if ast.data != "parameter_list":
            raise CompilerBackendException("invalid parameter list type: " + ast.data)

        if len(ast.children) != 0:
            params = []

            for node in ast.children:
                var_name = node.children[1].children[0].value
                var_type = self.generate_type(node.children[0])

                params.append(f"{var_type} {var_name}")

                # Register parameter as a local variable so it can be referenced later in the function
                if self.locals != None:
                    self.locals[var_name] = self.parse_type(node.children[0])

            inner = ','.join(params)

            if method:
                class_name = self.context["class_name"]

                # Register self as a local variable
                if self.locals != None:
                    self.locals["self"] = Type("class", class_name, 1)
                # Add self as first parameter
                inner = f"struct {class_name}* self," + inner

            return f"({inner})"
        else:
            if method:
                class_name = self.context['method_data']['class_name']

                return f"(struct {class_name}* self)"
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
            for_block = self.generate_block(ast.children[2])

            return f"for(size_t {for_var}={for_start};{for_var}<{for_end};{for_var}++){{{for_block}}}"

        elif ast.data == "statement_variable_define":
            var_type = self.generate_type(ast.children[0])
            var_name = ast.children[1].children[0].value
            var_expr = self.generate_expression(ast.children[2])

            # Register local variable
            self.locals[var_name] = self.parse_type(ast.children[0])

            return f"{var_type} {var_name}={var_expr};"

        elif ast.data == "statement_variable_declare":
            var_type = self.generate_type(ast.children[0])
            var_name = ast.children[1].children[0].value

            # Register local variable
            self.locals[var_name] = self.parse_type(ast.children[0])

            if self.locals[var_name].type == "class" and self.locals[var_name].ptr == 0:
                class_name = ast.children[0].children[0].children[0].value
                compiled = f"{var_type} {var_name}={{0}};__class_{class_name}_init(&{var_name});"
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
            fn_name = self.generate_expression(ast.children[0])
            # TODO: make this handle methods (if it doesn't already)
            if fn_name == "this":
                fn_name = self.context["current_function"]

            if ast.children[0].data in ("expression_dot", "expression_arrow"):
                method = True
                # Set class name for generate_argument_list
                op = "&" if ast.children[0].data == "expression_dot" else ""
                self.context["class_name"] = op + self.generate_expression(ast.children[0].children[0])
            else:
                method = False

            fn_args = self.generate_argument_list(ast.children[1], method=method)

            return f"({fn_name}{fn_args})"

        elif ast.data == "expression_op_bin":
            expr_l = self.generate_expression(ast.children[0])
            expr_r = self.generate_expression(ast.children[2])
            type_l = self.infer_type(ast.children[0])
            type_r = self.infer_type(ast.children[2])

            if type_l == type_r and type_l.type == "class" and type_l.ptr == 0:
                # Check if class implemented overloading for operator
                fn_name = f"__{ast.children[1].data}__"
                if fn_name in self.export.classes[type_l.name].fns:
                    return f"(__class_{type_l.name}_{fn_name}(&{expr_l},&{expr_r}))"
            else:
                expr_op = OP_BIN_MAP[ast.children[1].data]
                return f"({expr_l}{expr_op}{expr_r})"

        elif ast.data == "expression_arrow":
            expr = self.generate_expression(ast.children[0])
            name = ast.children[1].children[0].value
            return f"({expr}->{name})"

        elif ast.data == "expression_dot":
            expr = self.generate_expression(ast.children[0])
            name = ast.children[1].children[0].value
            return f"({expr}.{name})"

        elif ast.data == "expression_value":
            value = ast.children[0].children[0].value
            return f"({value})"

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
            return Type("class", ast.children[0].children[0].value, ptr)
        else:
            raise CompilerBackendException("can't parse unknown type type: " + ast.data)

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
            # TODO
            raise CompilerBackendException("don't know how to infer function call type")

        elif ast.data == "expression_op_bin":
            type_l = self.infer_type(ast.children[0])
            type_r = self.infer_type(ast.children[2])

            if type_l == type_r:
                return type_l
            else:
                raise CompilerBackendException("can't apply binary operation to expressions of different type")

        elif ast.data == "expression_arrow":
            type_l = self.infer_type(ast.children[0])
            name = ast.children[1].children[0].value

            if not (type_l.type == "class" and type_l.ptr == 1):
                raise CompilerBackendException("left side of arrow expression is not pointer to class")

            return self.export.classes[type_l.name].vars[name]

        elif ast.data == "expression_dot":
            # TODO
            raise CompilerBackendException("don't know how to infer dot expression type")

        elif ast.data == "expression_value":
            type = ast.children[0].data

            if type == "ident":
                return self.locals[ast.children[0].children[0].value]

            return VALUE_TYPE_MAP[type]

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