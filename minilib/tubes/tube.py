# -*- coding: utf-8 -*-
import abc
import logging
import os
import re
import string
import subprocess
import sys
import threading
import time

from minitools import atexit
from minitools.context import context
import minitools.log as log
from minitools.timeout import Timeout
from minitools.tubes.buffer import Buffer
from minitools.util import packing


class tube(Timeout):
    """
    Container of all the tube functions common to sockets, TTYs and SSH connetions.
    """

    default = Timeout.default
    forever = Timeout.forever

    def __init__(self, timeout = default, level = None, newline = b"\n", *a, **kw):
        super(tube, self).__init__(timeout)

        self.buffer = Buffer(*a, **kw)
        self._newline = newline
        self.newline = newline
        atexit.register(self.close)

    # Functions based on functions from subclasses
    def recv(self, numb = None, timeout = default):
        numb = self.buffer.get_fill_size(numb)
        return self._recv(numb, timeout) or b''

    def unrecv(self, data):
        data = packing._need_bytes(data)
        self.buffer.unget(data)

    def _fillbuffer(self, timeout = default):
        data = b''

        with self.local(timeout):
            data = self.recv_raw(self.buffer.get_fill_size())

        if data:
            log.debug('Received %#x bytes:' % len(data))
            log.maybe_hexdump(data)
        if data:
            self.buffer.add(data)

        return data


    def _recv(self, numb = None, timeout = default):
        numb = self.buffer.get_fill_size(numb)

        # No buffered data, could not put anything in the buffer
        # before timeout.
        if not self.buffer and not self._fillbuffer(timeout):
            return b''

        return self.buffer.get(numb)


    def recvn(self, numb, timeout = default):
        # Keep track of how much data has been received
        # It will be pasted together at the end if a
        # timeout does not occur, or put into the tube buffer.
        with self.countdown(timeout):
            while self.countdown_active() and len(self.buffer) < numb and self._fillbuffer(self.timeout):
                pass

        if len(self.buffer) < numb:
            return b''

        return self.buffer.get(numb)

    def recvuntil(self, delims, drop=False, timeout=default):
        # Convert string into singleton tupple
        if isinstance(delims, (bytes, bytearray, str)):
            delims = (delims,)
        delims = tuple(map(packing._need_bytes, delims))

        # Longest delimiter for tracking purposes
        longest = max(map(len, delims))

        # Cumulative data to search
        data = []
        top = b''

        with self.countdown(timeout):
            while self.countdown_active():
                try:
                    res = self.recv(timeout=self.timeout)
                except Exception:
                    self.unrecv(b''.join(data) + top)
                    raise

                if not res:
                    self.unrecv(b''.join(data) + top)
                    return b''

                top += res
                start = len(top)
                for d in delims:
                    j = top.find(d)
                    if start > j > -1:
                        start = j
                        end = j + len(d)
                if start < len(top):
                    self.unrecv(top[end:])
                    if drop:
                        top = top[:start]
                    else:
                        top = top[:end]
                    return b''.join(data) + top
                if len(top) > longest:
                    i = -longest - 1
                    data.append(top[:i])
                    top = top[i:]

        return b''

    def recvline(self, keepends=True, timeout=default):
        try:
            return self.recvuntil(self.newline, drop = not keepends, timeout = timeout)
        except EOFError:
            if not context.throw_eof_on_incomplete_line and self.buffer.size > 0:
                if context.throw_eof_on_incomplete_line is None:
                    log.warn_once('EOFError during recvline. Returning buffered data without trailing newline.')
                return self.buffer.get()
            raise

    

    ## SENDING


    def send(self, data):
        data = packing._need_bytes(data)

        log.debug('Sent %#x bytes:' % len(data))
        log.maybe_hexdump(data)

        self.send_raw(data)

    def sendline(self, line=b''):
        line = packing._need_bytes(line)

        self.send(line + self.newline)


    def sendafter(self, delim, data, timeout = default):
        data = packing._need_bytes(data)
        res = self.recvuntil(delim, timeout=timeout)
        self.send(data)
        return res

    def sendlineafter(self, delim, data, timeout = default):
        data = packing._need_bytes(data)
        res = self.recvuntil(delim, timeout=timeout)
        self.sendline(data)
        return res


    def interactive(self, prompt = '$'+ ' '):
        log.info('Switching to interactive mode')

        go = threading.Event()
        def recv_thread():
            while not go.is_set():
                try:
                    cur = self.recv(timeout = 0.05)
                    cur = cur.replace(self.newline, b'\n')
                    if cur:
                        stdout = sys.stdout
                        stdout = getattr(stdout, 'buffer', stdout)
                        stdout.write(cur)
                        stdout.flush()
                except EOFError:
                    log.info('Got EOF while reading in interactive')
                    break

        t = context.Thread(target = recv_thread)
        t.daemon = True
        t.start()

        try:
            os_linesep = os.linesep.encode()
            to_skip = b''
            while not go.is_set():
                stdin = getattr(sys.stdin, 'buffer', sys.stdin)
                data = stdin.read(1)
                # Keep OS's line separator if NOTERM is set and
                # the user did not specify a custom newline
                # even if stdin is a tty.
                if sys.stdin.isatty() and (
                    context.newline != b"\n"
                    or self._newline is not None
                ):
                    if to_skip:
                        if to_skip[:1] != data:
                            data = os_linesep[: -len(to_skip)] + data
                        else:
                            to_skip = to_skip[1:]
                            if to_skip:
                                continue
                            data = self.newline
                    # If we observe a prefix of the line separator in a tty,
                    # assume we'll see the rest of it immediately after.
                    # This could stall until the next character is seen if
                    # the line separator is started but never finished, but
                    # that is unlikely to happen in a dynamic tty.
                    elif data and os_linesep.startswith(data):
                        if len(os_linesep) > 1:
                            to_skip = os_linesep[1:]
                            continue
                        data = self.newline

                if data:
                    try:
                        self.send(data)
                    except EOFError:
                        go.set()
                        log.info('Got EOF while sending in interactive')
                else:
                    go.set()
        except KeyboardInterrupt:
            log.info('Interrupted')
            go.set()

        while t.is_alive():
            t.join(timeout = 0.1)

    def stream(self, line_mode=True):
        buf = Buffer()
        function = self.recvline if line_mode else self.recv
        try:
            while True:
                buf.add(function())
                stdout = sys.stdout
                stdout = getattr(stdout, 'buffer', stdout)
                stdout.write(buf.data[-1])
        except KeyboardInterrupt:
            pass
        except EOFError:
            pass

        return buf.get()

    shutdown_directions = {
        'in':    'recv',
        'read':  'recv',
        'recv':  'recv',
        'out':   'send',
        'write': 'send',
        'send':  'send',
    }

    connected_directions = shutdown_directions.copy()
    connected_directions['any'] = 'any'

    def shutdown(self, direction = "send"):
        try:
            direction = self.shutdown_directions[direction]
        except KeyError:
            raise KeyError('direction must be in %r' % sorted(self.shutdown_directions))
        else:
            self.shutdown_raw(self.shutdown_directions[direction])

    def spawn_process(self, *args, **kwargs):
        return subprocess.Popen(
            *args,
            stdin = self.fileno(),
            stdout = self.fileno(),
            stderr = self.fileno(),
            **kwargs
        )


    # The minimal interface to be implemented by a child
    @abc.abstractmethod
    def recv_raw(self, numb):
        raise EOFError('Not implemented')

    @abc.abstractmethod
    def send_raw(self, data):
        raise EOFError('Not implemented')

    def settimeout_raw(self, timeout):
        raise NotImplementedError()

    def timeout_change(self):
        try:
            self.settimeout_raw(self.timeout)
        except NotImplementedError:
            pass

    def can_recv_raw(self, timeout):
        raise NotImplementedError()

    def connected_raw(self, direction):
        raise NotImplementedError()

    def close(self):
        pass
        # Ideally we could:
        # raise NotImplementedError()
        # But this causes issues with the unit tests.

    def fileno(self):
        raise NotImplementedError()

    def shutdown_raw(self, direction):
        raise NotImplementedError()

    # Dynamic functions

    def make_wrapper(func):
        def wrapperb(self, *a, **kw):
            return bytearray(func(self, *a, **kw))
        def wrapperS(self, *a, **kw):
            return packing._decode(func(self, *a, **kw))
        wrapperb.__name__ = func.__name__ + 'b'
        wrapperS.__name__ = func.__name__ + 'S'
        return wrapperb, wrapperS

    for func in [recv,
                 recvn,
                 recvuntil,
                 recvline,
                ]:
        for wrapper in make_wrapper(func):
            locals()[wrapper.__name__] = wrapper

    def make_wrapper(func, alias):
        def wrapper(self, *a, **kw):
            return func(self, *a, **kw)
        wrapper.__name__ = alias
        return wrapper

    for _name in list(locals()):
        if 'recv' in _name:
            _name2 = _name.replace('recv', 'read')
        elif 'send' in _name:
            _name2 = _name.replace('send', 'write')
        else:
            continue
        locals()[_name2] = make_wrapper(locals()[_name], _name2)

    # Clean up the scope
    del wrapper, func, make_wrapper, _name, _name2
