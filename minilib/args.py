import collections
import logging
import os
import string
import sys
import minilib.log

class PwnlibArgs(collections.defaultdict):
	def __getattr__(self, attr):
		if attr.startswith('_'):
			raise AttributeError(attr)
		return self[attr]

args = PwnlibArgs(str)
term_mode  = True
env_prefix = 'PWNLIB_'
free_form  = True


def isident(s):
	first = string.ascii_uppercase + '_'
	body = string.digits + first
	if not s:
		return False
	if s[0] not in first:
		return False
	if not all(c in body for c in s[1:]):
		return False
	return True

def DEBUG(x):
	minilib.log.verbose = True

hooks = {
	'DEBUG': DEBUG,
}

def initialize():
	for k, v in os.environ.items():
		if not k.startswith(env_prefix):
			continue
		k = k[len(env_prefix):]

		if k in hooks:
			hooks[k](v)
		elif isident(k):
			args[k] = v

	argv = sys.argv[:]
	for arg in sys.argv[:]:
		orig  = arg
		value = 'True'

		if '=' in arg:
			arg, value = arg.split('=', 1)

		if arg in hooks:
			sys.argv.remove(orig)
			hooks[arg](value)

		elif free_form and isident(arg):
			sys.argv.remove(orig)
			args[arg] = value