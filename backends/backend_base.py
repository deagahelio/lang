from .exceptions import CompilerBackendException

class BaseBackend:
    def __init__(self, args):
        self.args = args
        self.file = args.file
        self.output = args.output

    def generate(self, ast):
        print(ast.pretty())
        raise CompilerBackendException("no generation function implemented for backend")

    def write_output(self):
        raise CompilerBackendException("no output function implemented for backend")