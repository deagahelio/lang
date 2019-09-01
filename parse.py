from lark import Lark

class Parser:
    def __init__(self, grammar_file_path):
        with open(grammar_file_path, "r") as f:
            self.lark = Lark(f.read(), start="program", parser="lalr")

    def parse(self, code, start="program"):
        return self.lark.parse(code, start=start)
    
    def parse_file(self, file_path):
        with open(file_path, "r") as f:
            ast = self.parse(f.read())
        return ast