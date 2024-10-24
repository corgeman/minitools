import socket
from minilib.timeout import Timeout
from minilib.tubes.sock import sock
import minilib.log as log

class remote(sock):
    def __init__(self, host, port,
                 fam = "any", typ = "tcp",
                 sock=None, ssl=False, ssl_context=None, ssl_args=None, sni=True,
                 *args, **kwargs):
        super(remote, self).__init__(*args, **kwargs)

        # convert port to string for sagemath support
        self.rport  = str(port)
        self.rhost  = host

        if sock:
            self.family = sock.family
            self.type   = sock.type
            self.proto  = sock.proto
            self.sock   = sock

        else:
            typ = self._get_type(typ)
            fam = self._get_family(fam)
            try:
                self.sock   = self._connect(fam, typ)
            except socket.gaierror as e:
                if e.errno != socket.EAI_NONAME:
                    raise
                log.error('Could not resolve hostname: %r' % host)
        if self.sock:
            log.info("Connected to remote server")
            self.settimeout(self.timeout)
            self.lhost, self.lport = self.sock.getsockname()[:2]

            if ssl:
                # Deferred import to save startup time
                import ssl as _ssl

                ssl_args = ssl_args or {}
                ssl_context = ssl_context or _ssl.SSLContext(_ssl.PROTOCOL_TLSv1_2)
                if isinstance(sni, str):
                    ssl_args["server_hostname"] = sni
                elif sni:
                    ssl_args["server_hostname"] = host
                self.sock = ssl_context.wrap_socket(self.sock,**ssl_args)

    def _connect(self, fam, typ):
        sock    = None
        timeout = self.timeout

        log.info('Opening connection to %s on port %s' % (self.rhost, self.rport))
        for res in socket.getaddrinfo(self.rhost, self.rport, fam, typ, 0, socket.AI_PASSIVE):
            self.family, self.type, self.proto, _canonname, sockaddr = res

            if self.type not in [socket.SOCK_STREAM, socket.SOCK_DGRAM]:
                continue

            log.info("Trying %s" % sockaddr[0])

            sock = socket.socket(self.family, self.type, self.proto)

            if timeout is not None and timeout <= 0:
                sock.setblocking(0)
            else:
                sock.setblocking(1)
                sock.settimeout(timeout)

            try:
                sock.connect(sockaddr)
                return sock
            except socket.error:
                pass
        log.error("Could not connect to %s on port %s" % (self.rhost, self.rport))

    @classmethod
    def fromsocket(cls, socket):
        s = socket
        host, port = s.getpeername()
        return remote(host, port, fam=s.family, typ=s.type, sock=s)

class tcp(remote):
    __doc__ = remote.__doc__
    def __init__(self, host, port, *a, **kw):
        return super(tcp, self).__init__(host, port, typ="tcp", *a, **kw)

class udp(remote):
    __doc__ = remote.__doc__
    def __init__(self, host, port, *a, **kw):
        return super(udp, self).__init__(host, port, typ="udp", *a, **kw)

class connect(remote):
    __doc__ = remote.__doc__