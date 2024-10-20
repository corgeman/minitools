 # -*- coding: utf-8 -*-
import collections
import struct
import sys
import warnings

from minitools.context import context
import itertools

mod = sys.modules[__name__]

#
# Make individual packers, e.g. _p8lu
#
ops   = ['p','u']
sizes = {8:'B', 16:'H', 32:'I', 64:'Q'}

op_verbs = {'p': 'pack', 'u': 'unpack'}


def make_single(op,size):
    name = '%s%s' % (op, size)
    fmt  = sizes[size].upper()
    fmt = '<'+fmt

    struct_op = getattr(struct.Struct(fmt), op_verbs[op])
    if op == 'u':
        def routine(data, stacklevel=1):
            return struct_op(data)[0]
    else:
        def routine(data, stacklevel=None):
            return struct_op(data)
    routine.__name__ = routine.__qualname__ = name

    return name, routine

for op,size in itertools.product(ops, sizes):
    name, routine = make_single(op,size)
    setattr(mod, name, routine)

def _fit(pieces, preprocessor, packer, filler, stacklevel=1):

    # Pulls bytes from `filler` and adds them to `pad` until it ends in `key`.
    # Returns the index of `key` in `pad`.
    pad = bytearray()
    def fill(key):
        key = bytearray(key)
        offset = pad.find(key)
        while offset == -1:
            pad.append(next(filler))
            offset = pad.find(key, -len(key))
        return offset

    # Key conversion:
    # - convert str/unicode keys to offsets
    # - convert large int (no null-bytes in a machine word) keys to offsets
    pieces_ = dict()
    large_key = 2**(context.word_size-8)
    for k, v in pieces.items():
        if isinstance(k, (int,)):
            if k >= large_key:
                k = fill(pack(k))
        elif isinstance(k, (str, bytearray, bytes)):
            k = fill(_need_bytes(k, stacklevel, 0x80))
        else:
            raise TypeError("flat(): offset must be of type int or str, but got '%s'" % type(k))
        if k in pieces_:
            raise ValueError("flag(): multiple values at offset %d" % k)
        pieces_[k] = v
    pieces = pieces_

    # We must "roll back" `filler` so each recursive call to `_flat` gets it in
    # the right position
    filler = itertools.chain(pad, filler)

    # Build output
    out = b''

    # Negative indices need to be removed and then re-submitted
    negative = {k:v for k,v in pieces.items() if isinstance(k, int) and k<0}

    for k in negative:
        del pieces[k]

    # Positive output
    for k, v in sorted(pieces.items()):
        if k < len(out):
            raise ValueError("flat(): data at offset %d overlaps with previous data which ends at offset %d" % (k, len(out)))

        # Fill up to offset
        while len(out) < k:
            out += p8(next(filler))

        # Recursively flatten data
        out += _flat([v], preprocessor, packer, filler, stacklevel + 1)

    # Now do negative indices
    out_negative = b''
    if negative:
        most_negative = min(negative.keys())
        for k, v in sorted(negative.items()):
            k += -most_negative

            if k < len(out_negative):
                raise ValueError("flat(): data at offset %d overlaps with previous data which ends at offset %d" % (k, len(out)))

            # Fill up to offset
            while len(out_negative) < k:
                out_negative += p8(next(filler))

            # Recursively flatten data
            out_negative += _flat([v], preprocessor, packer, filler, stacklevel + 1)

    return filler, out_negative + out

def _flat(args, preprocessor, packer, filler, stacklevel=1):
    out = []
    for arg in args:

        if not isinstance(arg, (list, tuple, dict)):
            arg_ = preprocessor(arg)
            if arg_ is not None:
                arg = arg_

        if hasattr(arg, '__flat__'):
            val = arg.__flat__()
        elif isinstance(arg, (list, tuple)):
            val = _flat(arg, preprocessor, packer, filler, stacklevel + 1)
        elif isinstance(arg, dict):
            filler, val = _fit(arg, preprocessor, packer, filler, stacklevel + 1)
        elif isinstance(arg, bytes):
            val = arg
        elif isinstance(arg, str):
            val = _need_bytes(arg, stacklevel + 1)
        elif isinstance(arg, (int,)):
            val = packer(arg)
        elif isinstance(arg, bytearray):
            val = bytes(arg)
        else:
            raise ValueError("flat(): Flat does not support values of type %s" % type(arg))

        out.append(val)

        # Advance `filler` for "non-recursive" values
        if not isinstance(arg, (list, tuple, dict)):
            for _ in range(len(val)):
                next(filler)

    return b''.join(out)

def flat(*args, **kwargs):
    # HACK: To avoid circular imports we need to delay the import of `cyclic`
    from minitools.util import cyclic

    preprocessor = kwargs.pop('preprocessor', lambda x: None)
    filler       = kwargs.pop('filler', cyclic.de_bruijn())
    length       = kwargs.pop('length', None)
    stacklevel   = kwargs.pop('stacklevel', 0)

    if isinstance(filler, (str)):
        filler = bytearray(_need_bytes(filler))

    if kwargs != {}:
        raise TypeError("flat() does not support argument %r" % kwargs.popitem()[0])

    filler = itertools.cycle(filler)
    out = _flat(args, preprocessor, make_packer(), filler, stacklevel + 2)

    if length:
        if len(out) > length:
            raise ValueError("flat(): Arguments does not fit within `length` (= %d) bytes" % length)
        out += b''.join(p8(next(filler)) for _ in range(length - len(out)))

    return out


def signed(integer):
    return unpack(pack(integer), signed=True)

def unsigned(integer):
    return unpack(pack(integer))


def _need_bytes(s, level=1, min_wrong=0):
    if isinstance(s, (bytes, bytearray)):
        return s   # already bytes

    encoding = context.encoding
    errors = 'strict'
    worst = -1
    if encoding == 'auto':
        worst = s and max(map(ord, s)) or 0
        if worst > 255:
            encoding = 'UTF-8'
            errors = 'surrogateescape'
        elif worst > 127:
            encoding = 'ISO-8859-1'
        else:
            encoding = 'ASCII'

    if worst >= min_wrong:
        warnings.warn("Text is not bytes; assuming {}, no guarantees. See https://docs.pwntools.com/#bytes"
                      .format(encoding), BytesWarning, level + 2)
    return s.encode(encoding, errors)

def _need_text(s, level=1):
    if isinstance(s, (str)):
        return s   # already text

    if not isinstance(s, (bytes, bytearray)):
        return repr(s)

    encoding = context.encoding
    errors = 'strict'
    if encoding == 'auto':
        for encoding in 'ASCII', 'UTF-8', 'ISO-8859-1':
            try:
                s.decode(encoding)
            except UnicodeDecodeError:
                pass
            else:
                break

    warnings.warn("Bytes is not text; assuming {}, no guarantees. See https://docs.pwntools.com/#bytes"
                  .format(encoding), BytesWarning, level + 2)
    return s.decode(encoding, errors)

def _encode(s):
    if isinstance(s, (bytes, bytearray)):
        return s   # already bytes

    if context.encoding == 'auto':
        try:
            return s.encode('latin1')
        except UnicodeEncodeError:
            return s.encode('utf-8', 'surrogateescape')
    return s.encode(context.encoding)

def _decode(b):
    if isinstance(b, (str)):
        return b   # already text

    if context.encoding == 'auto':
        try:
            return b.decode('utf-8')
        except UnicodeDecodeError:
            return b.decode('latin1')
        except AttributeError:
            return b
    return b.decode(context.encoding)

del op, size
del name, routine, mod
