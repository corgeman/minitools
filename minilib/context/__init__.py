# Todo: Work on A) Figuring out how this module works and B) how to compress it as much as possible.
# (Right now, it's just copypasted)

import atexit
import collections
import errno
import functools
import logging
import os
import os.path
import platform
import shutil
import socket
import string
import sys
import tempfile
import threading
import time


from minilib.timeout import Timeout

def _longest(d):
	return collections.OrderedDict((k,d[k]) for k in sorted(d, key=len, reverse=True))

def _validator(validator):
	name = validator.__name__
	doc  = validator.__doc__

	def fget(self):
		return self._tls[name]

	def fset(self, val):
		self._tls[name] = validator(self, val)

	def fdel(self):
		self._tls._current.pop(name,None)

	return property(fget, fset, fdel, doc)

class Thread(threading.Thread):
	def __init__(self, *args, **kwargs):
		super(Thread, self).__init__(*args, **kwargs)
		self.old = context.copy()

	def __bootstrap(self):
		context.update(**self.old)
		sup = super(Thread, self)
		bootstrap = getattr(sup, '_bootstrap', None)
		if bootstrap is None:
			sup.__bootstrap()
		else:
			bootstrap()
	_bootstrap = __bootstrap

class _defaultdict(dict):
	def __init__(self, default=None):
		super(_defaultdict, self).__init__()
		if default is None:
			default = {}

		self.default = default


	def __missing__(self, key):
		return self.default[key]

class _DictStack(object):
	def __init__(self, default):
		self._current = _defaultdict(default)
		self.__stack  = []

	def push(self):
		self.__stack.append(self._current.copy())

	def pop(self):
		self._current.clear()
		self._current.update(self.__stack.pop())

	def copy(self):
		return self._current.copy()

	# Pass-through container emulation routines
	def __len__(self):              return self._current.__len__()
	def __delitem__(self, k):       return self._current.__delitem__(k)
	def __getitem__(self, k):       return self._current.__getitem__(k)
	def __setitem__(self, k, v):    return self._current.__setitem__(k, v)
	def __contains__(self, k):      return self._current.__contains__(k)
	def __iter__(self):             return self._current.__iter__()
	def __repr__(self):             return self._current.__repr__()
	def __eq__(self, other):        return self._current.__eq__(other)

	# Required for keyword expansion operator ** to work
	def keys(self):                 return self._current.keys()
	def values(self):               return self._current.values()
	def items(self):                return self._current.items()

class _Tls_DictStack(threading.local, _DictStack):
	pass


class ContextType(object):
	#
	# Use of 'slots' is a heavy-handed way to prevent accidents
	# like 'context.architecture=' instead of 'context.arch='.
	#
	# Setting any properties on a ContextType object will throw an
	# exception.
	#
	__slots__ = '_tls',

	#: Default values for :class:`pwnlib.context.ContextType`
	defaults = {
		'adb_host': 'localhost',
		'adb_port': 5037,
		'arch': 'amd64',
		'aslr': True,
		'binary': None,
		'bits': 32,
		'buffer_size': 4096,
		'cache_dir_base': os.environ.get(
			'XDG_CACHE_HOME',
			os.path.join(os.path.expanduser('~'), '.cache')
		),
		'cyclic_alphabet': string.ascii_lowercase.encode(),
		'cyclic_size': 4,
		'delete_corefiles': False,
		'device': os.getenv('ANDROID_SERIAL', None) or None,
		'encoding': 'auto',
		'endian': 'little',
		'gdbinit': "",
		'kernel': None,
		'local_libcdb': "/var/lib/libc-database",
		'log_level': logging.INFO,
		'log_console': sys.stdout,
		'randomize': False,
		'rename_corefiles': True,
		'newline': b'\n',
		'throw_eof_on_incomplete_line': None,
		'noptrace': False,
		'os': 'linux',
		'proxy': None,
		'ssh_session': None,
		'signed': False,
		'terminal': tuple(),
		'timeout': Timeout.maximum,
	}

	unix_like    = {'newline': b'\n'}
	windows_like = {'newline': b'\r\n'}

	#: Keys are valid values for :meth:`pwnlib.context.ContextType.os`
	oses = _longest({
		'linux':     unix_like,
		'freebsd':   unix_like,
		'windows':   windows_like,
		'cgc':       unix_like,
		'android':   unix_like,
		'baremetal': unix_like,
		'darwin':    unix_like,
	})

	big_32    = {'endian': 'big', 'bits': 32}
	big_64    = {'endian': 'big', 'bits': 64}
	little_8  = {'endian': 'little', 'bits': 8}
	little_16 = {'endian': 'little', 'bits': 16}
	little_32 = {'endian': 'little', 'bits': 32}
	little_64 = {'endian': 'little', 'bits': 64}

	#: Keys are valid values for :meth:`pwnlib.context.ContextType.arch`.
	#
	#: Values are defaults which are set when
	#: :attr:`pwnlib.context.ContextType.arch` is set
	architectures = _longest({
		'aarch64':   little_64,
		'alpha':     little_64,
		'avr':       little_8,
		'amd64':     little_64,
		'arm':       little_32,
		'cris':      little_32,
		'i386':      little_32,
		'ia64':      big_64,
		'm68k':      big_32,
		'mips':      little_32,
		'mips64':    little_64,
		'msp430':    little_16,
		'powerpc':   big_32,
		'powerpc64': big_64,
		'riscv32':   little_32,
		'riscv64':   little_64,
		's390':      big_32,
		'sparc':     big_32,
		'sparc64':   big_64,
		'thumb':     little_32,
		'vax':       little_32,
		'none':      {},
	})

	#: Valid values for :attr:`endian`
	endiannesses = _longest({
		'be':     'big',
		'eb':     'big',
		'big':    'big',
		'le':     'little',
		'el':     'little',
		'little': 'little'
	})

	#: Valid string values for :attr:`signed`
	signednesses = {
		'unsigned': False,
		'no':       False,
		'yes':      True,
		'signed':   True
	}

	valid_signed = sorted(signednesses)

	def __init__(self, **kwargs):
		self._tls = _Tls_DictStack(_defaultdict(self.defaults))
		self.update(**kwargs)


	def copy(self):
		return self._tls.copy()


	@property
	def __dict__(self):
		return self.copy()

	def update(self, *args, **kwargs):
		for arg in args:
			self.update(**arg)

		for k,v in kwargs.items():
			setattr(self,k,v)

	def __repr__(self):
		v = sorted("%s = %r" % (k,v) for k,v in self._tls._current.items())
		return '%s(%s)' % (self.__class__.__name__, ', '.join(v))

	def local(self, function=None, **kwargs):
		class LocalContext(object):
			def __enter__(a):
				self._tls.push()
				self.update(**{k:v for k,v in kwargs.items() if v is not None})
				return self

			def __exit__(a, *b, **c):
				self._tls.pop()

			def __call__(self, function, *a, **kw):
				@functools.wraps(function)
				def inner(*a, **kw):
					with self:
						return function(*a, **kw)
				return inner

		return LocalContext()

	@property
	def silent(self, function=None):
		return self.local(function, log_level='error')

	@property
	def quiet(self, function=None):
		level = 'error'
		if context.log_level <= logging.DEBUG:
			level = None
		return self.local(function, log_level=level)

	def quietfunc(self, function):
		@functools.wraps(function)
		def wrapper(*a, **kw):
			level = 'error'
			if context.log_level <= logging.DEBUG:
				level = None
			with self.local(function, log_level=level):
				return function(*a, **kw)
		return wrapper


	@property
	def verbose(self):
		return self.local(log_level='debug')

	def clear(self, *a, **kw):
		self._tls._current.clear()

		if a or kw:
			self.update(*a, **kw)

	@property
	def native(self):
		if context.os in ('android', 'baremetal', 'cgc'):
			return False

		arch = context.arch
		with context.local(arch = platform.machine()):
			platform_arch = context.arch

			if arch in ('i386', 'amd64') and platform_arch in ('i386', 'amd64'):
				return True

			return arch == platform_arch

	@_validator
	def arch(self, arch):
		# Lowercase
		arch = arch.lower()

		# Attempt to perform convenience and legacy compatibility transformations.
		# We have to make sure that x86_64 appears before x86 for this to work correctly.
		transform = [('ppc64', 'powerpc64'),
					 ('ppc', 'powerpc'),
					 ('x86-64', 'amd64'),
					 ('x86_64', 'amd64'),
					 ('x86', 'i386'),
					 ('i686', 'i386'),
					 ('armv7l', 'arm'),
					 ('armeabi', 'arm'),
					 ('arm64', 'aarch64'),
					 ('rv32', 'riscv32'),
					 ('rv64', 'riscv64')]
		for k, v in transform:
			if arch.startswith(k):
				arch = v
				break

		try:
			defaults = self.architectures[arch]
		except KeyError:
			raise AttributeError('AttributeError: arch (%r) must be one of %r' % (arch, sorted(self.architectures)))

		for k,v in defaults.items():
			if k not in self._tls:
				self._tls[k] = v

		return arch

	@_validator
	def aslr(self, aslr):
		return bool(aslr)

	@_validator
	def kernel(self, arch):
		with self.local(arch=arch):
			return self.arch

	@_validator
	def bits(self, bits):
		bits = int(bits)

		if bits <= 0:
			raise AttributeError("bits must be > 0 (%r)" % bits)

		return bits

	@_validator
	def binary(self, binary):
		from minilib.elf.elf import ELF
		# print(binary)

		if not isinstance(binary, ELF):
			binary = ELF(binary)

		# self.arch   = binary.arch
		self.bits   = binary.bits
		self.endian = binary.endianness
		# self.os     = 

		return binary

	@property
	def bytes(self):
		return self.bits // 8
	@bytes.setter
	def bytes(self, value):
		self.bits = value*8

	@_validator
	def encoding(self, charset):
		if charset == 'auto':
			return charset

		if (  b'aA'.decode(charset) != 'aA'
			or 'aA'.encode(charset) != b'aA'):
			raise ValueError('Strange encoding!')

		return charset

	@_validator
	def endian(self, endianness):
		endian = endianness.lower()

		if endian not in self.endiannesses:
			raise AttributeError("endian must be one of %r" % sorted(self.endiannesses))

		return self.endiannesses[endian]


	@_validator
	def log_level(self, value):
		# If it can be converted into an int, success
		try:                    return int(value)
		except ValueError:  pass

		# If it is defined in the logging module, success
		try:                    return getattr(logging, value.upper())
		except AttributeError:  pass

		# Otherwise, fail
		try:
			level_names = logging._levelToName.values()
		except AttributeError:
			level_names = filter(lambda x: isinstance(x,str), logging._levelNames)
		permitted = sorted(level_names)
		raise AttributeError('log_level must be an integer or one of %r' % permitted)

	@_validator
	def log_file(self, value):
		if isinstance(value, (bytes, str)):
			# check if mode was specified as "[value],[mode]"
			from minilib.util.packing import _need_text
			value = _need_text(value)
			if ',' not in value:
				value += ',a'
			filename, mode = value.rsplit(',', 1)
			value = open(filename, mode)

		elif not hasattr(value, "fileno"):
			raise AttributeError('log_file must be a file')

		# Is this the same file we already have open?
		# If so, don't re-print the banner.
		if self.log_file and not isinstance(self.log_file, _devnull):
			a = os.fstat(value.fileno()).st_ino
			b = os.fstat(self.log_file.fileno()).st_ino

			if a == b:
				return self.log_file

		iso_8601 = '%Y-%m-%dT%H:%M:%S'
		lines = [
			'=' * 78,
			' Started at %s ' % time.strftime(iso_8601),
			' sys.argv = [',
			]
		for arg in sys.argv:
			lines.append('   %r,' % arg)
		lines.append(' ]')
		lines.append('=' * 78)
		for line in lines:
			value.write('=%-78s=\n' % line)
		value.flush()
		return value

	@_validator
	def log_console(self, stream):
		if isinstance(stream, str):
			stream = open(stream, 'wt')
		return stream

	@_validator
	def local_libcdb(self, path):
		if not os.path.isdir(path):
			raise AttributeError("'%s' does not exist, please download libc-database first" % path)

		return path

	@property
	def mask(self):
		return (1 << self.bits) - 1

	@_validator
	def os(self, os):
		os = os.lower()

		try:
			defaults = self.oses[os]
		except KeyError:
			raise AttributeError("os must be one of %r" % sorted(self.oses))

		for k,v in defaults.items():
			if k not in self._tls:
				self._tls[k] = v

		return os

	@_validator
	def randomize(self, r):
		"""
		Global flag that lots of things should be randomized.
		"""
		return bool(r)

	@_validator
	def signed(self, signed):
		try:             signed = self.signednesses[signed]
		except KeyError: pass

		if isinstance(signed, str):
			raise AttributeError('signed must be one of %r or a non-string truthy value' % sorted(self.signednesses))

		return bool(signed)

	@_validator
	def timeout(self, value=Timeout.default):
		return Timeout(value).timeout

	@_validator
	def terminal(self, value):
		if isinstance(value, (bytes, str)):
			return [value]
		return value

	@property
	def abi(self):
		return self._abi

	@_validator
	def proxy(self, proxy):
		if not proxy:
			socket.socket = _original_socket
			return None

		if isinstance(proxy, str):
			proxy = (socks.SOCKS5, proxy)

		if not isinstance(proxy, Iterable):
			raise AttributeError('proxy must be a string hostname, or tuple of arguments for socks.set_default_proxy')

		socks.set_default_proxy(*proxy)
		socket.socket = socks.socksocket

		return proxy

	@_validator
	def noptrace(self, value):
		return bool(value)


	@_validator
	def adb_host(self, value):
		return str(value)


	@_validator
	def adb_port(self, value):
		return int(value)

	@_validator
	def device(self, device):
		if isinstance(device, (bytes, str)):
			device = Device(device)
		if isinstance(device, Device):
			self.arch = device.arch or self.arch
			self.bits = device.bits or self.bits
			self.endian = device.endian or self.endian
			self.os = device.os or self.os
		elif device is not None:
			raise AttributeError("device must be either a Device object or a serial number as a string")

		return device

	@property
	def adb(self):
		ADB_PATH = os.environ.get('ADB_PATH', 'adb')

		command = [ADB_PATH]

		if self.adb_host != self.defaults['adb_host']:
			command += ['-H', self.adb_host]

		if self.adb_port != self.defaults['adb_port']:
			command += ['-P', str(self.adb_port)]

		if self.device:
			command += ['-s', str(self.device)]

		return command

	@_validator
	def buffer_size(self, size):
		return int(size)

	@_validator
	def cache_dir_base(self, new_base):
		if new_base != self.cache_dir_base:
			del self._tls["cache_dir"]
		if os.access(new_base, os.F_OK) and not os.access(new_base, os.W_OK):
			raise OSError(errno.EPERM, "Cache base dir is not writable")
		return new_base

	@property
	def cache_dir(self):
		try:
			# If the TLS already has a cache directory path, we return it
			# without any futher checks since it must have been valid when it
			# was set and if that has changed, hiding the TOCTOU here would be
			# potentially confusing
			return self._tls["cache_dir"]
		except KeyError:
			pass

		# Attempt to create a Python version specific cache dir and its parents
		cache_dirname = '.pwntools-cache-%d.%d' % sys.version_info[:2]
		cache_dirpath = os.path.join(self.cache_dir_base, cache_dirname)
		try:
			os.makedirs(cache_dirpath)
		except OSError as exc:
			# If we failed for any reason other than the cache directory
			# already existing then we'll fall back to a temporary directory
			# object which doesn't respect the `cache_dir_base`
			if exc.errno != errno.EEXIST:
				try:
					cache_dirpath = tempfile.mkdtemp(prefix=".pwntools-tmp")
				except IOError:
					# This implies no good candidates for temporary files so we
					# have to return `None`
					return None
				else:
					# Ensure the temporary cache dir is cleaned up on exit. A
					# `TemporaryDirectory` would do this better upon garbage
					# collection but this is necessary for Python 2 support.
					atexit.register(shutil.rmtree, cache_dirpath)
		# By this time we have a cache directory which exists but we don't know
		# if it is actually writable. Some wargames e.g. pwnable.kr have
		# created dummy directories which cannot be modified by the user
		# account (owned by root).
		if os.access(cache_dirpath, os.W_OK):
			# Stash this in TLS for later reuse
			self._tls["cache_dir"] = cache_dirpath
			return cache_dirpath
		else:
			return None

	@cache_dir.setter
	def cache_dir(self, v):
		if os.access(v, os.W_OK):
			# Stash this in TLS for later reuse
			self._tls["cache_dir"] = v

	@_validator
	def delete_corefiles(self, v):
		return bool(v)

	@_validator
	def rename_corefiles(self, v):
		return bool(v)

	@_validator
	def newline(self, v):
		# circular imports
		from minilib.util.packing import _need_bytes
		return _need_bytes(v)
	
	@_validator
	def throw_eof_on_incomplete_line(self, v):
		return v if v is None else bool(v)


	@_validator
	def gdbinit(self, value):
		return str(value)

	@_validator
	def cyclic_alphabet(self, alphabet):
		# Do not allow multiple occurrences
		if len(set(alphabet)) != len(alphabet):
			raise AttributeError("cyclic alphabet cannot contain duplicates")

		return alphabet.encode()

	@_validator
	def cyclic_size(self, size):
		size = int(size)

		if size > self.bytes:
			raise AttributeError("cyclic pattern size cannot be larger than word size")

		return size

	#*************************************************************************
	#                               ALIASES
	#*************************************************************************
	#
	# These fields are aliases for fields defined above, either for
	# convenience or compatibility.
	#
	#*************************************************************************

	def __call__(self, **kwargs):
		return self.update(**kwargs)

	def reset_local(self):
		self.clear()

	@property
	def endianness(self):
		return self.endian
	@endianness.setter
	def endianness(self, value):
		self.endian = value


	@property
	def sign(self):
		return self.signed

	@sign.setter
	def sign(self, value):
		self.signed = value

	@property
	def signedness(self):
		return self.signed

	@signedness.setter
	def signedness(self, value):
		self.signed = value


	@property
	def word_size(self):
		return self.bits

	@word_size.setter
	def word_size(self, value):
		self.bits = value

	Thread = Thread

context = ContextType()