import sys
import argparse

import parse
import backends

GRAMMAR_FILE_PATH = "grammar.lark"

if __name__ == "__main__":
    backend_list_pretty = ", ".join(backends.BACKEND_MAP)

    parser = argparse.ArgumentParser(description="Compiles some code.")
    parser.add_argument("file", help="file to compile")
    parser.add_argument("-b", "--backend", default="c", help="code generation backend to use. available: " + backend_list_pretty)
    parser.add_argument("-o", "--output", required=True, help="output file to write to")
    parser.add_argument("--keep-intermediate", action="store_true", help="keeps the intermediate transpiled source file (for backends that support it)")
    args = parser.parse_args()

    args.backend = args.backend.lower()

    if args.backend not in backends.BACKEND_MAP:
        print(f"error: {args.backend} isn't a valid backend. available: {backend_list_pretty}")
        sys.exit(1)
    
    parser = parse.Parser(GRAMMAR_FILE_PATH)
    ast = parser.parse_file(args.file)

    backend = backends.BACKEND_MAP[args.backend](args)
    backend.generate(ast)
    backend.write_output()