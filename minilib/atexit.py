import sys
import threading
import traceback
import atexit as std_atexit

from minilib.context import context

__all__ = ['register', 'unregister']

_lock = threading.Lock()
_ident = 0
_handlers = {}

def register(func, *args, **kwargs):
	global _ident
	with _lock:
		ident = _ident
		_ident += 1
	_handlers[ident] = (func, args, kwargs, vars(context))
	return ident

def unregister(ident):
	if ident in _handlers:
		del _handlers[ident]

def _run_handlers():
	context.clear()
	for _ident, (func, args, kwargs, ctx) in \
		sorted(_handlers.items(), reverse = True):
		try:
			with context.local(**ctx):
				func(*args, **kwargs)
		except SystemExit:
			pass
		except Exception:
			# extract the current exception and rewind the traceback to where it
			# originated
			typ, val, tb = sys.exc_info()
			traceback.print_exception(typ, val, tb.tb_next)

# if there's already an exitfunc registered be sure to run that too
if hasattr(sys, "exitfunc"):
	register(sys.exitfunc)

if sys.version_info[0] < 3:
	sys.exitfunc = _run_handlers
else:
	std_atexit.register(_run_handlers)

