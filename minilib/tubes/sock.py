import errno
import select
import socket

from minitools.tubes.tube import tube
import minitools.log as log

class sock(tube):
    def __init__(self, *args, **kwargs):
        super(sock, self).__init__(*args, **kwargs)
        self.closed = {"recv": False, "send": False}

    # Overwritten for better usability
    def recvall(self, timeout = tube.forever):
        if getattr(self, 'type', None) == socket.SOCK_DGRAM:
            log.error("UDP sockets does not supports recvall")
        else:
            return super(sock, self).recvall(timeout)

    def recv_raw(self, numb, *a):
        if self.closed["recv"]:
            raise EOFError

        while True:
            try:
                data = self.sock.recv(numb, *a)
                break
            except socket.timeout:
                return None
            except IOError as e:
                if e.errno in (errno.EAGAIN, errno.ETIMEDOUT) or 'timed out' in e.strerror:
                    return None
                elif e.errno in (errno.ECONNREFUSED, errno.ECONNRESET):
                    self.shutdown("recv")
                    raise EOFError
                elif e.errno == errno.EINTR:
                    continue
                else:
                    raise

        if not data:
            self.shutdown("recv")
            raise EOFError

        return data

    def send_raw(self, data):
        if self.closed["send"]:
            raise EOFError

        try:
            self.sock.sendall(data)
        except IOError as e:
            eof_numbers = (errno.EPIPE, errno.ECONNRESET, errno.ECONNREFUSED)
            if e.errno in eof_numbers or 'Socket is closed' in e.args:
                self.shutdown("send")
                raise EOFError
            else:
                raise

    def settimeout_raw(self, timeout):
        sock = getattr(self, 'sock', None)
        if sock:
            sock.settimeout(timeout)

    def can_recv_raw(self, timeout):
        if not self.sock or self.closed["recv"]:
            return False

        # select() will tell us data is available at EOF
        can_recv = select.select([self.sock], [], [], timeout) == ([self.sock], [], [])

        if not can_recv:
            return False

        # Ensure there's actually data, not just EOF
        try:
            self.recv_raw(1, socket.MSG_PEEK)
        except EOFError:
            return False

        return True


    def close(self):
        sock = getattr(self, 'sock', None)
        if not sock:
            return

        # Mark as closed in both directions
        self.closed['send'] = True
        self.closed['recv'] = True

        sock.close()
        self.sock = None
        self._close_msg()

    def _close_msg(self):
        log.info('Closed connection to %s port %s', self.rhost, self.rport)

    def fileno(self):
        if not self.sock:
            log.error("A closed socket does not have a file number")

        return self.sock.fileno()

    def shutdown_raw(self, direction):
        if self.closed[direction]:
            return

        self.closed[direction] = True

        if direction == "send":
            try:
                self.sock.shutdown(socket.SHUT_WR)
            except IOError as e:
                if e.errno == errno.ENOTCONN:
                    pass
                else:
                    raise

        if direction == "recv":
            try:
                self.sock.shutdown(socket.SHUT_RD)
            except IOError as e:
                if e.errno == errno.ENOTCONN:
                    pass
                else:
                    raise

        if False not in self.closed.values():
            self.close()

    @classmethod
    def _get_family(cls, fam):
        if fam == 'any':
            fam = socket.AF_UNSPEC
        elif fam.lower() in ['ipv4', 'ip4', 'v4', '4']:
            fam = socket.AF_INET
        elif fam.lower() in ['ipv6', 'ip6', 'v6', '6']:
            fam = socket.AF_INET6
        else:
            log.error("%s(): socket family %r is not supported",
                       cls.__name__,
                       fam)

        return fam

    @classmethod
    def _get_type(cls, typ):
        if typ == "tcp":
            typ = socket.SOCK_STREAM
        elif typ == "udp":
            typ = socket.SOCK_DGRAM
        else:
            log.error("%s(): socket type %r is not supported",
                       cls.__name__,
                       typ)

        return typ