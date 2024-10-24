# -*- coding: utf-8 -*-
import time

class _DummyContextClass(object):
	def __enter__(self):   pass
	def __exit__(self,*a): pass

_DummyContext = _DummyContextClass()

class _countdown_handler(object):
	def __init__(self, obj, timeout):
		self.obj     = obj
		self.timeout = timeout

	def __enter__(self):
		self.old_timeout  = self.obj._timeout
		self.old_stop     = self.obj._stop

		self.obj._stop    = time.time() + self.timeout

		if self.old_stop:
			self.obj._stop = min(self.obj._stop, self.old_stop)

		self.obj._timeout = self.timeout
		self.obj.timeout_change()
	def __exit__(self, *a):
		self.obj._timeout = self.old_timeout
		self.obj._stop    = self.old_stop
		self.obj.timeout_change()

class _local_handler(object):
	def __init__(self, obj, timeout):
		self.obj     = obj
		self.timeout = timeout
	def __enter__(self):
		self.old_timeout  = self.obj._timeout
		self.old_stop     = self.obj._stop

		self.obj._stop    = 0
		self.obj._timeout = self.timeout # leverage validation
		self.obj.timeout_change()

	def __exit__(self, *a):
		self.obj._timeout = self.old_timeout
		self.obj._stop    = self.old_stop
		self.obj.timeout_change()

class TimeoutDefault(object):
	def __repr__(self): return "pwnlib.timeout.Timeout.default"
	def __str__(self): return "<default timeout>"

class Maximum(float):
	def __repr__(self):
		return 'pwnlib.timeout.maximum'
maximum = Maximum(2**20)

class Timeout(object):
	#: Value indicating that the timeout should not be changed
	default = TimeoutDefault()

	#: Value indicating that a timeout should not ever occur
	forever = None

	#: Maximum value for a timeout.  Used to get around platform issues
	#: with very large timeouts.
	#:
	#: OSX does not permit setting socket timeouts to 2**22.
	#: Assume that if we receive a timeout of 2**21 or greater,
	#: that the value is effectively infinite.
	maximum = maximum

	def __init__(self, timeout=default):
		self._stop    = 0
		self.timeout = self._get_timeout_seconds(timeout)

	@property
	def timeout(self):
		timeout = self._timeout
		stop    = self._stop

		if not stop:
			return timeout

		return max(stop-time.time(), 0)

	@timeout.setter
	def timeout(self, value):
		assert not self._stop
		self._timeout = self._get_timeout_seconds(value)
		self.timeout_change()

	def _get_timeout_seconds(self, value):
		if value is self.default:
			value = Maximum(2**20)

		elif value is self.forever:
			value = self.maximum

		else:
			value = float(value)

			if value < 0:
				raise AttributeError("timeout: Timeout cannot be negative")

			if value > self.maximum:
				value = self.maximum
		return value

	def countdown_active(self):
		return (self._stop == 0) or (self._stop > time.time())

	def timeout_change(self):
		pass

	def countdown(self, timeout = default):
		# Don't count down from infinity
		if timeout is self.maximum:
			return _DummyContext

		if timeout is self.default and self.timeout is self.maximum:
			return _DummyContext

		if timeout is self.default:
			timeout = self._timeout

		return _countdown_handler(self, timeout)

	def local(self, timeout):
		if timeout is self.default or timeout == self.timeout:
			return _DummyContext

		return _local_handler(self, timeout)
