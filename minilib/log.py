from textwrap import wrap
import sys

verbose = False

def hexdump(text):
		info(''.join(wrap(text.hex(),20)))

def maybe_hexdump(text):
	if verbose:
		hexdump(text)

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

