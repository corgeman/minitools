from minilib.util import packing
import minilib.log as log
import sys, stat, os

def which(name, all = False, path=None):
    # If name is a path, do not attempt to resolve it.
    if os.path.sep in name:
        return name

    if sys.platform == 'win32':
        pathexts = os.environ.get('PATHEXT', '').split(os.pathsep)
        isroot = False
    else:
        pathexts = []
        isroot = os.getuid() == 0
    pathexts = [''] + pathexts
    out = set()
    try:
        path = path or os.environ['PATH']
    except KeyError:
        log.exception('Environment variable $PATH is not set')
    for path_part in path.split(os.pathsep):
        for ext in pathexts:
            nameext = name + ext
            p = os.path.join(path_part, nameext)
            if os.access(p, os.X_OK):
                st = os.stat(p)
                if not stat.S_ISREG(st.st_mode):
                    continue
                # work around this issue: https://bugs.python.org/issue9311
                if isroot and not \
                st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                    continue
                if all:
                    out.add(p)
                    break
                else:
                    return p
    if all:
        return out
    else:
        return None

def normalize_argv_env(argv, env, log, level=2):
    #
    # Validate argv
    #
    # - Must be a list/tuple of strings
    # - Each string must not contain '\x00'
    #
    argv = argv or []

    if isinstance(argv, (str, bytes)):
        argv = [argv]

    if not isinstance(argv, (list, tuple)):
        log.error('argv must be a list or tuple: %r' % argv)

    if not all(isinstance(arg, (str, bytes, bytearray)) for arg in argv):
        log.error("argv must be strings or bytes: %r" % argv)

    # Create a duplicate so we can modify it
    argv = list(argv)

    for i, oarg in enumerate(argv):
        arg = packing._need_bytes(oarg, level, 0x80)  # ASCII text is okay
        if b'\x00' in arg[:-1]:
            log.error('Inappropriate nulls in argv[%i]: %r' % (i, oarg))
        argv[i] = bytearray(arg.rstrip(b'\x00'))

    #
    # Validate environment
    #
    # - Must be a dictionary of {string:string}
    # - No strings may contain '\x00'
    #

    # Create a duplicate so we can modify it safely
    env2 = []
    if hasattr(env, 'items'):
        env_items = env.items()
    else:
        env_items = env
    if env:
        for k,v in env_items:
            if not isinstance(k, (bytes,)):
                log.error('Environment keys must be strings: %r' % k)
            # Check if = is in the key, Required check since we sometimes call ctypes.execve directly
            # https://github.com/python/cpython/blob/025995feadaeebeef5d808f2564f0fd65b704ea5/Modules/posixmodule.c#L6476
            if b'=' in packing._encode(k):
                log.error('Environment keys may not contain "=": %r' % (k))
            if not isinstance(v, (bytes,)):
                log.error('Environment values must be strings: %r=%r' % (k,v))
            k = packing._need_bytes(k, level, 0x80)  # ASCII text is okay
            v = packing._need_bytes(v, level, 0x80)  # ASCII text is okay
            if b'\x00' in k[:-1]:
                log.error('Inappropriate nulls in env key: %r' % (k))
            if b'\x00' in v[:-1]:
                log.error('Inappropriate nulls in env value: %r=%r' % (k, v))
            env2.append((bytearray(k.rstrip(b'\x00')), bytearray(v.rstrip(b'\x00'))))

    return argv, env2 or env