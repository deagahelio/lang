# lang

A programming language.

```rust
include "stdio"

fn fibonacci(n u32) u32 {
    if n < 2 {
        return 1
    } else {
        return this(n - 1) + this(n - 2)
    }
}

fn main() i32 {
    for n in 0..9 {
        printf("%d\n", fibonacci(n))
    }
    return 0
}
```

## Usage

`main.py [-h] [-b/--backend BACKEND] [-o/--output OUTPUT_FILE] [--keep-intermediate] source_file`

## Dependencies

- lark-parser