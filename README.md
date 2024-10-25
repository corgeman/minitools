# minitools
A portable, highly-compressed (~30KB) version of pwntools. Meant to be transferred to systems with little or no internet access.

## Usage
Running `create_minified.sh` will create a file `minitools.zip` that `sys` can import as a module. Once imported, begin your exploit as usual:
```python
import sys
sys.path.insert(0, 'minitools.zip')
from mini import *
# continue your exploit script as normal
exe = ELF("./vulnerable_program")
libc = ELF("./libc.so.6")
p = remote("doesnotexist.com",9001)
...
```
`pwn` has been renamed to `mini` and `pwnlib` to `minilib`. Other than that, nothing should significantly change about your exploit code.

## Features
Minitools includes:
- Most of `remote()` and `process()` (minus uncommon functions like `recvline_regex`)
- Common packing functions (`u64`, `u32`, `p64`, `p32`, `flat` should also work)
- `cyclic()` and `cyclic_find()`
- Format string payload generation
- Argument parsing
- A mediocre implementation of logging
- A mediocre implementation of `ELF()`

Minitools does not include:
- Pwntools' 'gdb' module
- ROP chain generation (I may attempt this later-- x86 only though)
- Support for non-x86 architectures
- Windows support
- The 'pwn' command-line tool
- Python2 support (it's been 16 years, c'mon)

## Transferring via clipboard
30KB is small enough to comfortably fit on your clipboard, meaning all you need to transfer this to a remote system is a shell on it!
Compress `minitools.zip` a little further with `gzip`, then base64 encode it:
```bash
cat minitools.zip | gzip | base64 -w 0
```
Copy this big blob of base64 onto your clipboard. Then, on the remote system:
```bash
echo "<CTRL+V HERE>" | base64 -d | gunzip > minitools.zip
```
You've now transferred this library with nothing but your terminal!

## Warning
This repository is *very* hacky. I reccomend reading `WARNING.md` for some alternative solutions.