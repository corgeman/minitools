# -*- coding: utf-8 -*-
import ctypes
import errno
import logging
import os
import select
import signal
import stat
import subprocess
import sys
import time
from collections import namedtuple

IS_WINDOWS = sys.platform.startswith('win')

if IS_WINDOWS:
	raise Exception("minitools does not support windows")
import fcntl
import pty
import resource
import tty

from minilib.context import context
from minilib.timeout import Timeout
from minilib.tubes.tube import tube
from minilib.util.misc import which
from minilib.util.misc import normalize_argv_env
from minilib.util.packing import _decode
import minilib.log as log

class PTY(object): pass
PTY=PTY()
STDOUT = subprocess.STDOUT
PIPE = subprocess.PIPE

signal_names = {-v:k for k,v in signal.__dict__.items() if k.startswith('SIG')}

class process(tube):
	STDOUT = STDOUT
	PIPE = PIPE
	PTY = PTY

	#: Have we seen the process stop?  If so, this is a unix timestamp.
	_stop_noticed = 0

	proc = None

	def __init__(self, argv = None,
				 shell = False,
				 executable = None,
				 cwd = None,
				 env = None,
				 ignore_environ = None,
				 stdin  = PIPE,
				 stdout = PTY if not IS_WINDOWS else PIPE,
				 stderr = STDOUT,
				 close_fds = True,
				 preexec_fn = lambda: None,
				 raw = True,
				 aslr = None,
				 setuid = None,
				 where = 'local',
				 display = None,
				 alarm = None,
				 creationflags = 0,
				 *args,
				 **kwargs
				 ):
		super(process, self).__init__(*args,**kwargs)

		# Permit using context.binary
		if argv is None:
			if context.binary:
				argv = [context.binary.path]
			else:
				raise TypeError('Must provide argv or set context.binary')

		#: :class:`subprocess.Popen` object that backs this process
		self.proc = None

		# We need to keep a copy of the un-_validated environment for printing
		original_env = env

		if shell:
			executable_val, argv_val, env_val = executable, argv, env
			if executable is None:
				executable_val = '/bin/sh'
		else:
			executable_val, argv_val, env_val = self._validate(cwd, executable, argv, env)

		# Avoid the need to have to deal with the STDOUT magic value.
		if stderr is STDOUT:
			stderr = stdout

		# Determine which descriptors will be attached to a new PTY
		handles = (stdin, stdout, stderr)

		#: Which file descriptor is the controlling TTY
		self.pty          = handles.index(PTY) if PTY in handles else None

		#: Whether the controlling TTY is set to raw mode
		self.raw          = raw

		#: Whether ASLR should be left on
		self.aslr         = aslr if aslr is not None else context.aslr

		#: Whether setuid is permitted
		self._setuid      = setuid if setuid is None else bool(setuid)

		# Create the PTY if necessary
		stdin, stdout, stderr, master, slave = self._handles(*handles)

		internal_preexec_fn = self.__preexec_fn

		#: Arguments passed on argv
		self.argv = argv_val

		#: Full path to the executable
		self.executable = executable_val

		if ignore_environ is None:
			ignore_environ = env is not None  # compat

		#: Environment passed on envp
		self.env = {} if ignore_environ else dict(getattr(os, "environb", os.environ))

		# Add environment variables as needed
		self.env.update(env_val or {})

		self._cwd = os.path.realpath(cwd or os.path.curdir)

		#: Alarm timeout of the process
		self.alarm        = alarm

		self.preexec_fn = preexec_fn
		self.display    = display or self.program
		self._qemu      = False
		self._corefile  = None

		message = "Starting %s process %r" % (where, self.display)

		if log.verbose:
			if argv != [self.executable]: message += ' argv=%r ' % self.argv
			if original_env not in (os.environ, None):  message += ' env=%r ' % self.env

		log.info(message)
		if not self.aslr:
			log.warn_once("ASLR is disabled!")

		# In the event the binary is a foreign architecture,
		# and binfmt is not installed (e.g. when running on
		# Travis CI), re-try with qemu-XXX if we get an
		# 'Exec format error'.
		prefixes = [([], self.executable)]
		exception = None

		for prefix, executable in prefixes:
			try:
				args = self.argv
				if prefix:
					args = prefix + args
				self.proc = subprocess.Popen(args = args,
												shell = shell,
												executable = executable,
												cwd = cwd,
												env = self.env,
												stdin = stdin,
												stdout = stdout,
												stderr = stderr,
												close_fds = close_fds,
												preexec_fn = internal_preexec_fn,
												creationflags = creationflags)
				break
			except OSError as exception:
				if exception.errno != errno.ENOEXEC:
					raise
				prefixes.append(self.__on_enoexec(exception))

		log.info("Process opened!")
		if self.pty is not None:
			if stdin is slave:
				self.proc.stdin = os.fdopen(os.dup(master), 'r+b', 0)
			if stdout is slave:
				self.proc.stdout = os.fdopen(os.dup(master), 'r+b', 0)
			if stderr is slave:
				self.proc.stderr = os.fdopen(os.dup(master), 'r+b', 0)

			os.close(master)
			os.close(slave)

		# Set in non-blocking mode so that a call to call recv(1000) will
		# return as soon as a the first byte is available
		if self.proc.stdout:
			fd = self.proc.stdout.fileno()
			fl = fcntl.fcntl(fd, fcntl.F_GETFL)
			fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

		# Save off information about whether the binary is setuid / setgid
		self.suid = self.uid = os.getuid()
		self.sgid = self.gid = os.getgid()
		st = os.stat(self.executable)
		if self._setuid:
			if (st.st_mode & stat.S_ISUID):
				self.suid = st.st_uid
			if (st.st_mode & stat.S_ISGID):
				self.sgid = st.st_gid

	def __preexec_fn(self):
		"""
		Routine executed in the child process before invoking execve().

		Handles setting the controlling TTY as well as invoking the user-
		supplied preexec_fn.
		"""
		if self.pty is not None:
			self.__pty_make_controlling_tty(self.pty)

		if not self.aslr:
			try:
				if context.os == 'linux' and self._setuid is not True:
					ADDR_NO_RANDOMIZE = 0x0040000
					ctypes.CDLL('libc.so.6').personality(ADDR_NO_RANDOMIZE)

				resource.setrlimit(resource.RLIMIT_STACK, (-1, -1))
			except Exception:
				self.exception("Could not disable ASLR")

		# Assume that the user would prefer to have core dumps.
		try:
			resource.setrlimit(resource.RLIMIT_CORE, (-1, -1))
		except Exception:
			pass

		# Given that we want a core file, assume that we want the whole thing.
		try:
			with open('/proc/self/coredump_filter', 'w') as f:
				f.write('0xff')
		except Exception:
			pass

		if self._setuid is False:
			try:
				PR_SET_NO_NEW_PRIVS = 38
				ctypes.CDLL('libc.so.6').prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
			except Exception:
				pass

		# Avoid issues with attaching to processes when yama-ptrace is set
		try:
			PR_SET_PTRACER = 0x59616d61
			PR_SET_PTRACER_ANY = -1
			ctypes.CDLL('libc.so.6').prctl(PR_SET_PTRACER, PR_SET_PTRACER_ANY, 0, 0, 0)
		except Exception:
			pass


		if self.alarm is not None:
			signal.alarm(self.alarm)

		self.preexec_fn()

	@property
	def program(self):
		"""Alias for ``executable``, for backward compatibility.

		Example:

			>>> p = process('/bin/true')
			>>> p.executable == '/bin/true'
			True
			>>> p.executable == p.program
			True

		"""
		return self.executable

	@property
	def cwd(self):
		try:
			self._cwd = os.path.realpath(f'/proc/{self.pid:d}/cwd')
		except Exception:
			pass

		return self._cwd


	def _validate(self, cwd, executable, argv, env):
		"""
		Perform extended validation on the executable path, argv, and envp.

		Mostly to make Python happy, but also to prevent common pitfalls.
		"""

		orig_cwd = cwd
		cwd = cwd or os.path.curdir

		argv, env = normalize_argv_env(argv, env, self, 4)
		if env:
			if sys.platform == 'win32':
				# Windows requires that all environment variables be strings
				env = {_decode(k): _decode(v) for k, v in env}
			else:
				env = {bytes(k): bytes(v) for k, v in env}
		if argv:
			argv = list(map(bytes, argv))

		#
		# Validate executable
		#
		# - Must be an absolute or relative path to the target executable
		# - If not, attempt to resolve the name in $PATH
		#
		if not executable:
			if not argv:
				log.error("Must specify argv or executable")
			executable = argv[0]

		if not isinstance(executable, str):
			executable = executable.decode('utf-8')

		path = env and env.get(b'PATH')
		if path:
			path = path.decode()
		else:
			path = os.environ.get('PATH')
		# Do not change absolute paths to binaries
		if executable.startswith(os.path.sep):
			pass

		# If there's no path component, it's in $PATH or relative to the
		# target directory.
		#
		# For example, 'sh'
		elif os.path.sep not in executable and which(executable, path=path):
			executable = which(executable, path=path)

		# Either there is a path component, or the binary is not in $PATH
		# For example, 'foo/bar' or 'bar' with cwd=='foo'
		elif os.path.sep not in executable:
			tmp = executable
			executable = os.path.join(cwd, executable)
			log.warn_once("Could not find executable %r in $PATH, using %r instead" % (tmp, executable))

		# There is a path component and user specified a working directory,
		# it must be relative to that directory. For example, 'bar/baz' with
		# cwd='foo' or './baz' with cwd='foo/bar'
		elif orig_cwd:
			executable = os.path.join(orig_cwd, executable)

		if not os.path.exists(executable):
			log.error("%r does not exist"  % executable)
		if not os.path.isfile(executable):
			log.error("%r is not a file" % executable)
		if not os.access(executable, os.X_OK):
			log.error("%r is not marked as executable (+x)" % executable)

		return executable, argv, env

	def _handles(self, stdin, stdout, stderr):
		master = slave = None

		if self.pty is not None:
			# Normally we could just use PIPE and be happy.
			# Unfortunately, this results in undesired behavior when
			# printf() and similar functions buffer data instead of
			# sending it directly.
			#
			# By opening a PTY for STDOUT, the libc routines will not
			# buffer any data on STDOUT.
			master, slave = pty.openpty()

			if self.raw:
				# By giving the child process a controlling TTY,
				# the OS will attempt to interpret terminal control codes
				# like backspace and Ctrl+C.
				#
				# If we don't want this, we set it to raw mode.
				tty.setraw(master)
				tty.setraw(slave)

			if stdin is PTY:
				stdin = slave
			if stdout is PTY:
				stdout = slave
			if stderr is PTY:
				stderr = slave

		return stdin, stdout, stderr, master, slave

	def __getattr__(self, attr):
		"""Permit pass-through access to the underlying process object for
		fields like ``pid`` and ``stdin``.
		"""
		if not attr.startswith('_') and hasattr(self.proc, attr):
			return getattr(self.proc, attr)
		raise AttributeError("'process' object has no attribute '%s'" % attr)

	def kill(self):
		"""kill()

		Kills the process.
		"""
		self.close()

	def poll(self, block = False):
		"""poll(block = False) -> int

		Arguments:
			block(bool): Wait for the process to exit

		Poll the exit code of the process. Will return None, if the
		process has not yet finished and the exit code otherwise.
		"""

		# In order to facilitate retrieving core files, force an update
		# to the current working directory
		_ = self.cwd

		if block:
			self.wait_for_close()

		self.proc.poll()
		returncode = self.proc.returncode

		if returncode is not None and not self._stop_noticed:
			self._stop_noticed = time.time()
			signame = ''
			if returncode < 0:
				signame = ' (%s)' % (signal_names.get(returncode, 'SIG???'))

			log.info("Process %r stopped with exit code %d%s (pid %i)" % (self.display,
																  returncode,
																  signame,
																  self.pid))
		return returncode

	def communicate(self, stdin = None):
		"""communicate(stdin = None) -> str

		Calls :meth:`subprocess.Popen.communicate` method on the process.
		"""

		return self.proc.communicate(stdin)

	# Implementation of the methods required for tube
	def recv_raw(self, numb):
		# This is a slight hack. We try to notice if the process is
		# dead, so we can write a message.
		self.poll()

		if not self.connected_raw('recv'):
			raise EOFError

		if not self.can_recv_raw(self.timeout):
			return ''

		# This will only be reached if we either have data,
		# or we have reached an EOF. In either case, it
		# should be safe to read without expecting it to block.
		data = ''

		try:
			data = self.proc.stdout.read(numb)
		except IOError:
			pass

		if not data:
			self.shutdown("recv")
			raise EOFError

		return data

	def send_raw(self, data):
		# This is a slight hack. We try to notice if the process is
		# dead, so we can write a message.
		self.poll()

		if not self.connected_raw('send'):
			raise EOFError

		try:
			self.proc.stdin.write(data)
			self.proc.stdin.flush()
		except IOError:
			raise EOFError

	def settimeout_raw(self, timeout):
		pass

	def can_recv_raw(self, timeout):
		if not self.connected_raw('recv'):
			return False

		try:
			if timeout is None:
				return select.select([self.proc.stdout], [], []) == ([self.proc.stdout], [], [])

			return select.select([self.proc.stdout], [], [], timeout) == ([self.proc.stdout], [], [])
		except ValueError:
			# Not sure why this isn't caught when testing self.proc.stdout.closed,
			# but it's not.
			#
			#   File "/home/user/pwntools/pwnlib/tubes/process.py", line 112, in can_recv_raw
			#     return select.select([self.proc.stdout], [], [], timeout) == ([self.proc.stdout], [], [])
			# ValueError: I/O operation on closed file
			raise EOFError
		except select.error as v:
			if v.args[0] == errno.EINTR:
				return False

	def connected_raw(self, direction):
		if direction == 'any':
			return self.poll() is None
		elif direction == 'send':
			return self.proc.stdin and not self.proc.stdin.closed
		elif direction == 'recv':
			return self.proc.stdout and not self.proc.stdout.closed

	def close(self):
		if self.proc is None:
			return

		# First check if we are already dead
		self.poll()

		if not self._stop_noticed:
			try:
				self.proc.kill()
				self.proc.wait()
				self._stop_noticed = time.time()
				log.info('Stopped process %r (pid %i)' % (self.program, self.pid))
			except OSError:
				pass

		# close file descriptors
		for fd in [self.proc.stdin, self.proc.stdout, self.proc.stderr]:
			if fd is not None:
				try:
					fd.close()
				except IOError as e:
					if e.errno != errno.EPIPE and e.errno != errno.EINVAL:
						raise


	def fileno(self):
		if not self.connected():
			log.error("A stopped process does not have a file number")

		return self.proc.stdout.fileno()

	def shutdown_raw(self, direction):
		if direction == "send":
			self.proc.stdin.close()

		if direction == "recv":
			self.proc.stdout.close()

		if all(fp is None or fp.closed for fp in [self.proc.stdin, self.proc.stdout]):
			self.close()

	def __pty_make_controlling_tty(self, tty_fd):
		'''This makes the pseudo-terminal the controlling tty. This should be
		more portable than the pty.fork() function. Specifically, this should
		work on Solaris. '''

		child_name = os.ttyname(tty_fd)

		# Disconnect from controlling tty. Harmless if not already connected.
		try:
			fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
			if fd >= 0:
				os.close(fd)
		# which exception, shouldnt' we catch explicitly .. ?
		except OSError:
			# Already disconnected. This happens if running inside cron.
			pass

		os.setsid()

		# Verify we are disconnected from controlling tty
		# by attempting to open it again.
		try:
			fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
			if fd >= 0:
				os.close(fd)
				raise Exception('Failed to disconnect from '
					'controlling tty. It is still possible to open /dev/tty.')
		# which exception, shouldnt' we catch explicitly .. ?
		except OSError:
			# Good! We are disconnected from a controlling tty.
			pass

		# Verify we can open child pty.
		fd = os.open(child_name, os.O_RDWR)
		if fd < 0:
			raise Exception("Could not open child pty, " + child_name)
		else:
			os.close(fd)

		# Verify we now have a controlling tty.
		fd = os.open("/dev/tty", os.O_WRONLY)
		if fd < 0:
			raise Exception("Could not open controlling tty, /dev/tty")
		else:
			os.close(fd)

	def maps(self):
		raise Exception("Sorry, maps() is not implemented due to needing 'psutil'")

	def get_mapping(self, path_value, single=True):
		all_maps = self.maps()

		if single:
			for mapping in all_maps:
				if path_value == mapping.path:
					return mapping
			return None

		m_mappings = []
		for mapping in all_maps:
			if path_value == mapping.path:
				m_mappings.append(mapping)
		return m_mappings


	@property
	def libc(self):
		raise Exception(".libc is not implemented")


	@property
	def elf(self):
		import minilib.elf.elf
		return minilib.elf.elf.ELF(self.executable)

	@property
	def stdin(self):
		"""Shorthand for ``self.proc.stdin``

		See: :obj:`.process.proc`
		"""
		return self.proc.stdin
	@property
	def stdout(self):
		"""Shorthand for ``self.proc.stdout``

		See: :obj:`.process.proc`
		"""
		return self.proc.stdout
	@property
	def stderr(self):
		"""Shorthand for ``self.proc.stderr``

		See: :obj:`.process.proc`
		"""
		return self.proc.stderr

# Keep reading the process's output in a separate thread,
# since there's no non-blocking read in python on Windows.
def _read_in_thread(recv_queue, proc_stdout):
	try:
		while True:
			b = proc_stdout.read(1)
			if b:
				recv_queue.put(b)
			else:
				break
	except:
		# Ignore any errors during Python shutdown
		pass
