import string

from minitools.context import context
import minitools.log as log
import itertools
from minitools.util import packing



def take(n, iterable):
    return list(itertools.islice(iterable, n))

# Taken from https://en.wikipedia.org/wiki/De_Bruijn_sequence but changed to a generator
def de_bruijn(alphabet = None, n = None):
    if alphabet is None:
        alphabet = context.cyclic_alphabet
    if n is None:
        n = context.cyclic_size
    if isinstance(alphabet, bytes):
        alphabet = bytearray(alphabet)
    k = len(alphabet)
    a = [0] * k * n
    def db(t, p):
        if t > n:
            if n % p == 0:
                for j in range(1, p + 1):
                    yield alphabet[a[j]]
        else:
            a[t] = a[t - p]
            for c in db(t + 1, p):
                yield c

            for j in range(a[t - p] + 1, k):
                a[t] = j
                for c in db(t + 1, t):
                    yield c

    return db(1,1)

def cyclic(length = None, alphabet = None, n = None):
    if n is None:
        n = context.cyclic_size

    if alphabet is None:
        alphabet = context.cyclic_alphabet

    if length is not None and len(alphabet) ** n < length:
        log.error("Can't create a pattern length=%i with len(alphabet)==%i and n==%i",
                  length, len(alphabet), n)

    generator = de_bruijn(alphabet, n)
    out = iters.take(length, generator)

    return _join_sequence(out, alphabet)

def cyclic_find(subseq, alphabet = None, n = None):
    if n is None:
        n = context.cyclic_size

    if isinstance(subseq, (int,)):
        if subseq >= 2**(8*n):
            # Assumption: The user has given an integer that is more than 2**(8n) bits, but would otherwise fit within
            #  a register of size 2**(8m) where m is a multiple of four
            notice = ("cyclic_find() expected an integer argument <= {cap:#x}, you gave {gave:#x}\n"
                      "Unless you specified cyclic(..., n={fits}), you probably just want the first {n} bytes.\n"
                      "Truncating the data at {n} bytes.  Specify cyclic_find(..., n={fits}) to override this.").format(
                cap=2**(8*n)-1,
                gave=subseq,
                # The number of bytes needed to represent subseq, rounded to the next 4
                fits=int(round(float(subseq.bit_length()) / 32 + 0.5) * 32) // 8,
                n=n,
            )
            log.warn_once(notice)
            if context.endian == 'little':
                subseq &= 2**(8*n) - 1
            else:
                while subseq >= 2**(8*n):
                    subseq >>= 8*n
        subseq = packing.pack(subseq, bytes=n)
    subseq = packing._need_bytes(subseq, 2, 0x80)

    if len(subseq) != n:
        log.warn_once("cyclic_find() expected a %i-byte subsequence, you gave %r\n"
            "Unless you specified cyclic(..., n=%i), you probably just want the first %d bytes.\n"
            "Truncating the data at %d bytes.  Specify cyclic_find(..., n=%i) to override this.",
            n, subseq, len(subseq), n, n, len(subseq))
        subseq = subseq[:n]

    if alphabet is None:
        alphabet = context.cyclic_alphabet
    alphabet = packing._need_bytes(alphabet, 2, 0x80)

    if any(c not in alphabet for c in subseq):
        return -1

    n = n or len(subseq)

    return _gen_find(subseq, de_bruijn(alphabet, n))

def _gen_find(subseq, generator):
    if isinstance(subseq, bytes):
        subseq = bytearray(subseq)
    subseq = list(subseq)
    pos = 0
    saved = []

    for c in generator:
        saved.append(c)
        if len(saved) > len(subseq):
            saved.pop(0)
            pos += 1
        if saved == subseq:
            return pos
    return -1

def _join_sequence(seq, alphabet):
    if isinstance(alphabet, (str,)):
        return ''.join(seq)
    elif isinstance(alphabet, bytes):
        return bytes(bytearray(seq))
    else:
        return seq

class cyclic_gen(object):
    def __init__(self, alphabet = None, n = None):
        if n is None:
            n = context.cyclic_size

        if alphabet is None:
            alphabet = context.cyclic_alphabet

        self._generator = de_bruijn(alphabet, n)
        self._alphabet = alphabet
        self._total_length = 0
        self._n = n
        self._chunks = []

    def get(self, length = None):
        if length is not None:
            self._chunks.append(length)
            self._total_length += length
            if len(self._alphabet) ** self._n < self._total_length:
                log.error("Can't create a pattern length=%i with len(alphabet)==%i and n==%i",
                          self._total_length, len(self._alphabet), self._n)
            out = [next(self._generator) for _ in range(length)]
        else:
            self._chunks.append(float("inf"))
            out = list(self._generator)

        return _join_sequence(out, self._alphabet)

    def find(self, subseq):
        global_index = cyclic_find(subseq, self._alphabet, self._n)
        remaining_index = global_index
        for chunk_idx in range(len(self._chunks)):
            chunk = self._chunks[chunk_idx]
            if remaining_index < chunk:
                return (global_index, chunk_idx, remaining_index)
            remaining_index -= chunk
        return -1
