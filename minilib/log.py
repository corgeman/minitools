from textwrap import wrap
from minilib.util import packing
from io import BytesIO
from functools import partial
import sys, string

verbose = False

color	= lambda c,t: f'\x1b[{c:d}m{t}\x1b[m'
gray	= partial(color,30)
red		= partial(color,31)
green	= partial(color,32)

default_style = {
	'marker':       gray,
	'nonprintable': gray,
	'00':           red,
	'0a':           red,
	'ff':           green
}


# This is normally in util.fiddling but I've moved it here for now.
# All keyword arguments have been removed.
def hexdump_iter(fd):
	style     = {}
	highlight = []

	_style = style
	style = default_style.copy()
	style.update(_style)

	skipping    = False
	lines       = []
	last_unique = ''
	byte_width  = len('00 ')
	spacer      = ' '
	marker      = '│'

	def style_byte(by):
		hbyte = '%02x' % by
		b = packing.p8(by)
		abyte = chr(by) if isprint(b) else '·'
		if hbyte in style:
			st = style[hbyte]
		elif isprint(b):
			pass
		else:
			st = style.get('nonprintable')
		if st:
			hbyte = st(hbyte)
			abyte = st(abyte)
		return hbyte, abyte
	cache = [style_byte(b) for b in range(256)]
	numb = 0
	while True:
		offset = 0 + numb
		try:
			chunk = fd.read(16)
		except EOFError:
			chunk = b''
		if chunk == b'':
			break
		numb += len(chunk)
		if last_unique:
			same_as_last_line = (last_unique == chunk)
			last_unique = chunk
			if same_as_last_line:
				if not skipping:
					yield '*'
					skipping = True
				continue
		skipping = False
		last_unique = chunk
		hexbytes = ''
		printable = ''
		color_chars = 0
		abyte = abyte_previous = ''
		for i, b in enumerate(bytearray(chunk)):
			abyte_previous = abyte
			hbyte, abyte = cache[b]
			color_chars += len(hbyte) - 2
			if (i + 1) % 4 == 0 and i < 16 - 1:
				hbyte += spacer
				abyte_previous += abyte
				abyte = marker
			hexbytes += hbyte + ' '
			printable += abyte_previous
		if abyte != marker:
			printable += abyte
		line_fmt = '%%(offset)08x  %%(hexbytes)-%is │%%(printable)s│' % (
				(16 * byte_width)
				+ color_chars
				+ 3 )
		line = line_fmt % {'offset': offset, 'hexbytes': hexbytes, 'printable': printable}
		yield line
	line = "%08x" % (0 + numb)
	yield line

# See comment in hexdump_iter
def hexdump(s):
	s = packing.flat(s, stacklevel=1)
	return '\n'.join(hexdump_iter(BytesIO(s)))

def indented(text):
	print(f"    {text}")

def maybe_hexdump(message):
	if not verbose:
		return
	if len(set(message)) == 1 and len(message) > 1:
			indented('%r * %#x' % (message[:1], len(message)))
	elif len(message) == 1 or all(c in string.printable.encode() for c in message):
		for line in message.splitlines(True):
			indented(repr(line))
	else:
		indented(hexdump(message))

def warning(text):
	print(f"[\x1b[43mWARNING\x1b[0m] {text}")

def warn(text):
	warning(text)

def warn_once(text):
	warning(text)

def info(text):
	print(f"[\x1b[32;1m+\x1b[0m] {text}")

def error(text):
	print(f"[\x1b[41mERROR\x1b[0m] {text}")
	sys.exit()

def exception(text):
	error(text)

def debug(text):
	if verbose:
		print(f"[\x1b[34;1m*\x1b[0m] {text}")

def critical(text):
	print(f"[\x1b[41m!!\x1b[0m] {text}")

