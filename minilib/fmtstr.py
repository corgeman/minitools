import logging
import re
from operator import itemgetter

import minilib.log as log
from minilib.util.cyclic import *
from minilib.util.packing import *

SPECIFIER = {
	1: 'hhn',
	2: 'hn',
	4: 'n',
	8: 'lln',
}


SZMASK = { sz: (1 << (sz * 8)) - 1 for sz in SPECIFIER }

WRITE_SIZE = {
	"byte": 1,
	"short": 2,
	"int": 4,
	"long": 8,
}

# Pwntools needs a ~4000 line library (sortedcontainers) to get a sorted list
# Here is my 9000 IQ solution in six. Less lines means its faster right guys
class SortedList(list):
	def __init__(self,key):
		super().__init__()
		self.key = key
	def add(self, x):
		super().__init__(list(sorted(self+[x],key=self.key)))


def normalize_writes(writes):
	# make all writes flat
	writes = { address: flat(data) for address, data in writes.items() }

	# merge adjacent writes (and detect overlaps)
	merged = []
	prev_end = -1
	for address, data in sorted(writes.items(), key=itemgetter(0)):
		if address < prev_end:
			raise ValueError("normalize_writes(): data at offset %d overlaps with previous data which ends at offset %d" % (address, prev_end))

		if address == prev_end and merged:
			merged[-1] = (merged[-1][0], merged[-1][1] + data)
		else:
			merged.append((address, data))

		prev_end = address + len(data)

	return merged


class AtomWrite(object):
	__slots__ = ( "start", "size", "integer", "mask" )

	def __init__(self, start, size, integer, mask=None):
		if mask is None:
			mask = (1 << (8 * size)) - 1
		self.start = int(start)
		self.size = size
		self.integer = int(integer)
		self.mask = int(mask)

	def __len__(self):
		return self.size

	def __key(self):
		return (self.start, self.size, self.integer, self.mask)

	def __eq__(self, other):
		if not isinstance(other, AtomWrite):
			raise TypeError("comparision not supported between instances of '%s' and '%s'" % (type(self), type(other)))
		return self.__key() == other.__key()

	def __ne__(self, other):
		return not self.__eq__(other)

	def __hash__(self):
		return hash(self.__key())

	def __repr__(self):
		return "AtomWrite(start=%d, size=%d, integer=%#x, mask=%#x)" % (self.start, self.size, self.integer, self.mask)

	@property
	def bitsize(self):
		return self.size * 8

	@property
	def end(self):
		return self.start + self.size

	def compute_padding(self, counter):
		wanted = self.integer & self.mask
		padding = 0
		while True:
			diff = wanted ^ ((counter + padding) & self.mask)
			if not diff: break
			# this masks the least significant set bit and adds it to padding
			padding += diff & (diff ^ (diff - 1))
		return padding

	def replace(self, start=None, size=None, integer=None, mask=None):
		start = self.start if start is None else start
		size = self.size if size is None else size
		integer = self.integer if integer is None else integer
		mask = self.mask if mask is None else mask
		return AtomWrite(start, size, integer, mask)

	def union(self, other):
		assert other.start == self.end, "writes to combine must be continous"
		if context.endian == "little":
			newinteger = (other.integer << self.bitsize) | self.integer
			newmask = (other.mask << self.bitsize) | self.mask
		elif context.endian == "big":
			newinteger = (self.integer << other.bitsize) | other.integer
			newmask = (self.mask << other.bitsize) | other.mask
		return AtomWrite(self.start, self.size + other.size, newinteger, newmask)

	def __getslice__(self, i,  j):
		return self.__getitem__(slice(i, j))

	def __getitem__(self, i):
		if not isinstance(i, slice):
			if i < 0 or i >= self.size:
				raise IndexError("out of range [0, " + str(self.size) + "): " + str(i))
			i = slice(i,i+1)
		start, stop, step = i.indices(self.size)
		if step != 1:
			raise IndexError("slices with step != 1 not supported for AtomWrite")

		clip = (1 << ((stop - start) * 8)) - 1
		if context.endian == 'little':
			shift = start * 8
		elif context.endian == 'big':
			shift = (self.size - stop) * 8
		return AtomWrite(self.start + start, stop - start, (self.integer >> shift) & clip, (self.mask >> shift) & clip)

def make_atoms_simple(address, data, badbytes=frozenset()):
	data = bytearray(data)
	if not badbytes:
		return [AtomWrite(address + i, 1, d) for i, d in enumerate(data)]

	if any(x in badbytes for x in pack(address)):
		raise RuntimeError("impossible to avoid a bad byte in starting address %x" % address)

	i = 0
	out = []
	end = address + len(data)
	while i < len(data):
		candidate = AtomWrite(address + i, 1, data[i])
		while candidate.end < end and any(x in badbytes for x in pack(candidate.end)):
			candidate = candidate.union(AtomWrite(candidate.end, 1, data[i + candidate.size]))

		sz = min([s for s in SPECIFIER if s >= candidate.size] + [float("inf")])
		if candidate.start + sz > end:
			raise RuntimeError("impossible to avoid badbytes starting after offset %d (address %#x)" % (i, i + address))
		i += candidate.size
		candidate = candidate.union(AtomWrite(candidate.end, sz - candidate.size, 0, 0))
		out.append(candidate)
	return out


def merge_atoms_writesize(atoms, maxsize):
	assert maxsize in SPECIFIER, "write size must be supported by printf"

	out = []
	while atoms:
		# look forward to find atoms to merge with
		best = (1, atoms[0])
		candidate = atoms[0]
		for idx, atom in enumerate(atoms[1:]):
			if candidate.end != atom.start: break

			candidate = candidate.union(atom)
			if candidate.size > maxsize: break
			if candidate.size in SPECIFIER:
				best = (idx+2, candidate)

		out += [best[1]]
		atoms[:best[0]] = []
	return out

def find_min_hamming_in_range_step(prev, step, carry, strict):
	lower, upper, value = step
	carryadd = 1 if carry else 0

	valbyte = value & 0xFF
	lowbyte = lower & 0xFF
	upbyte = upper & 0xFF

	# if we can the requested byte without carry, do so
	# requiring strictness if possible is not a problem since strictness will cost at most a single byte
	# (so if we don't get our wanted byte without strictness, we may as well require it if possible)
	val_require_strict = valbyte > upbyte or valbyte == upbyte and strict
	if lowbyte + carryadd <= valbyte:
		if prev[(0, val_require_strict)]:
			prev_score, prev_val, prev_mask = prev[(0, val_require_strict)]
			return prev_score + 1, (prev_val << 8) | valbyte, (prev_mask << 8) | 0xFF

	# now, we have two options: pick the wanted byte (forcing carry), or pick something else
	# check which option is better
	lowcarrybyte = (lowbyte + carryadd) & 0xFF
	other_require_strict = lowcarrybyte > upbyte or lowcarrybyte == upbyte and strict
	other_require_carry = lowbyte + carryadd > 0xFF
	prev_for_val = prev[(1, val_require_strict)]
	prev_for_other = prev[(other_require_carry, other_require_strict)]
	if prev_for_val and (not prev_for_other or prev_for_other[0] <= prev_for_val[0] + 1):
		return prev_for_val[0] + 1, (prev_for_val[1] << 8) | valbyte, (prev_for_val[2] << 8) | 0xFF
	if prev_for_other:
		return prev_for_other[0], (prev_for_other[1] << 8) | lowcarrybyte, (prev_for_other[2] << 8)
	return None

def find_min_hamming_in_range(maxbytes, lower, upper, target):
	steps = []
	for _ in range(maxbytes):
		steps += [(lower, upper, target)]
		lower = lower >> 8
		upper = upper >> 8
		target = target >> 8

	# the initial state
	prev = {
		(False,False): (0, 0, 0),
		(False,True): None if upper == lower else (0, lower, 0),
		(True,False): None if upper == lower else (0, lower, 0),
		(True,True): None if upper <= lower + 1 else (0, lower + 1, 0)
	}
	for step in reversed(steps):
		prev = {
			(carry, strict): find_min_hamming_in_range_step(prev, step, carry, strict )
			for carry in [False, True]
			for strict in [False, True]
		}
	return prev[(False,False)]
#
# what we don't do:
#  - create new atoms that cannot be created by merging existing atoms
#  - optimize based on masks
def merge_atoms_overlapping(atoms, sz, szmax, numbwritten, overflows):
	if not szmax:
		szmax = max(SPECIFIER.keys())

	assert 1 <= overflows, "must allow at least one overflow"
	assert sz <= szmax, "sz must be smaller or equal to szmax"

	maxwritten = numbwritten + (1 << (8 * sz)) * overflows
	done = [False for _ in atoms]

	numbwritten_at = [numbwritten for _ in atoms]
	out = []
	for idx, atom in enumerate(atoms):
		if done[idx]: continue
		numbwritten_here = numbwritten_at[idx]

		# greedily find the best possible write at the current offset
		# the best write is the one which sets the largest number of target
		# bytes correctly
		candidate = AtomWrite(atom.start, 0, 0)
		best = (atom.size, idx, atom)
		for nextidx, nextatom in enumerate(atoms[idx:], idx):
			# if there is no atom immediately following the current candidate
			# that we haven't written yet, stop
			if done[nextidx] or candidate.end != nextatom.start:
				break

			# extend the candidate with the next atom.
			# check that we are still within the limits and that the candidate
			# can be written with a format specifier (this excludes non-power-of-2 candidate sizes)
			candidate = candidate.union(nextatom)
			if candidate.size not in SPECIFIER: continue
			if candidate.size > szmax: break

			# now approximate the candidate if it is larger than the always allowed size (sz),
			# taking the `maxwritten` constraint into account
			# this ensures that we don't write more than `maxwritten` bytes
			approxed = candidate
			score = candidate.size
			if approxed.size > sz:
				score, v, m = find_min_hamming_in_range(approxed.size, numbwritten_here, maxwritten, approxed.integer)
				approxed = candidate.replace(integer=v, mask=m)

			# if the current candidate sets more bytes correctly, save it
			if score > best[0]:
				best = (score, nextidx, approxed)

		_, nextidx, best_candidate = best
		numbwritten_here += best_candidate.compute_padding(numbwritten_here)
		if numbwritten_here > maxwritten:
			maxwritten = numbwritten_here
		offset = 0

		# for all atoms that we merged, check if all bytes are written already to update `done``
		# also update the numbwritten_at for all the indices covered by the current best_candidate
		for i, iatom in enumerate(atoms[idx:nextidx+1], idx):
			shift = iatom.size

			# if there are no parts in the atom's that are not written by the candidate,
			# mark it as done
			if not (iatom.mask & (~best_candidate[offset:offset+shift].mask)):
				done[i] = True
			else:
				# numbwritten_at is only relevant for atoms that aren't done yet,
				# so update it only in that case (done atoms are never processed again)
				numbwritten_at[i] = max(numbwritten_at[i], numbwritten_here)

			offset += shift

		# emit the best candidate
		out += [best_candidate]
	return out

def overlapping_atoms(atoms):
	prev = None
	for atom in sorted(atoms, key=lambda a: a.start):
		if not prev:
			prev = atom
			continue
		if prev.end > atom.start:
			yield prev, atom
		if atom.end > prev.end:
			prev = atom

class AtomQueue(object):
	def __init__(self, numbwritten):
		self.queues = { sz: SortedList(key=lambda atom: atom.integer) for sz in SPECIFIER.keys() }
		self.positions = { sz: 0 for sz in SPECIFIER }
		self.numbwritten = numbwritten

	def add(self, atom):
		self.queues[atom.size].add(atom)
		if atom.integer & SZMASK[atom.size] < self.numbwritten & SZMASK[atom.size]:
			self.positions[atom.size] += 1

	def pop(self):
		active_sizes = [ sz for sz,p in self.positions.items() if p < len(self.queues[sz]) ]
		if not active_sizes:
			try:
				sz_reset = min(sz for sz,q in self.queues.items() if q)
			except ValueError:
				return None

			self.positions[sz_reset] = 0
			active_sizes = [sz_reset]

		best_size = min(active_sizes, key=lambda sz: self.queues[sz][self.positions[sz]].compute_padding(self.numbwritten))
		best_atom = self.queues[best_size].pop(self.positions[best_size])
		self.numbwritten += best_atom.compute_padding(self.numbwritten)

		return best_atom

def sort_atoms(atoms, numbwritten):
	order = { atom: i for i,atom in enumerate(atoms) }

	depgraph = { atom: set() for atom in atoms }
	rdepgraph = { atom: set() for atom in atoms }
	for atom1,atom2 in overlapping_atoms(atoms):
		if order[atom1] < order[atom2]:
			depgraph[atom2].add(atom1)
			rdepgraph[atom1].add(atom2)
		else:
			depgraph[atom1].add(atom2)
			rdepgraph[atom2].add(atom1)

	queue = AtomQueue(numbwritten)

	for atom, deps in depgraph.items():
		if not deps:
			queue.add(atom)

	out = []
	while True:
		atom = queue.pop()
		if not atom: # we are done
			break

		out.append(atom)

		# add all atoms that now have no dependencies anymore to the queue
		for dep in rdepgraph.pop(atom):
			if atom not in depgraph[dep]:
				continue
			depgraph[dep].discard(atom)
			if not depgraph[dep]:
				queue.add(dep)

	return out

def make_payload_dollar(data_offset, atoms, numbwritten=0, countersize=4, no_dollars=False):
	data = b""
	fmt = ""

	counter = numbwritten

	if no_dollars:
		# since we can't dynamically offset, we have to increment manually the parameter index, use %c, so the number of bytes written is predictable
		fmt += "%c" * (data_offset - 1)
		# every %c write a byte, so we need to keep track of that to have the right pad
		counter += data_offset - 1

	for idx, atom in enumerate(atoms):
		# set format string counter to correct value
		padding = atom.compute_padding(counter)
		counter = (counter + padding) % (1 << (countersize * 8))
		if countersize == 32 and counter > 2147483600:
			log.warn("number of written bytes in format string close to 1 << 31. this will likely not work on glibc")
		if padding >= (1 << (countersize*8-1)):
			log.warn("padding is negative, this will not work on glibc")

		# perform write
		# if the padding is less than 3, it is more convenient to write it : [ len("cc") < len("%2c") ] , this could help save some bytes, if it is 3 it will take the same amout of bytes
		# we also add ( context.bytes * no_dollars ) because , "%nccccccccc%n...ptr1ptr2" is more convenient than %"n%8c%n...ptr1ccccccccptr2"
		if padding < 4 + context.bytes * no_dollars:
				fmt += "c" * padding
				## if do not padded with %{n}c  do not need to add something in data to use as argument, since  we are not using a printf argument
		else: 
			fmt += "%" + str(padding) + "c"

			if no_dollars:
				data += b'c' * context.bytes
			
		if no_dollars:
			fmt += "%" +  SPECIFIER[atom.size]
		else:
			fmt += "%" + str(data_offset + idx) + "$" + SPECIFIER[atom.size]

		data += pack(atom.start)

	return fmt.encode(), data

def make_atoms(writes, sz, szmax, numbwritten, overflows, strategy, badbytes):
	all_atoms = []
	for address, data in normalize_writes(writes):
		atoms = make_atoms_simple(address, data, badbytes)
		if strategy == 'small':
			atoms = merge_atoms_overlapping(atoms, sz, szmax, numbwritten, overflows)
		elif strategy == 'fast':
			atoms = merge_atoms_writesize(atoms, sz)
		else:
			raise ValueError("strategy must be either 'small' or 'fast'")
		atoms = sort_atoms(atoms, numbwritten)
		all_atoms += atoms
	return all_atoms

def fmtstr_split(offset, writes, numbwritten=0, write_size='byte', write_size_max='long', overflows=16, strategy="small", badbytes=frozenset(), no_dollars=False):
	if write_size not in ['byte', 'short', 'int']:
		log.error("write_size must be 'byte', 'short' or 'int'")

	if write_size_max not in ['byte', 'short', 'int', 'long']:
		log.error("write_size_max must be 'byte', 'short', 'int' or 'long'")

	sz = WRITE_SIZE[write_size]
	szmax = WRITE_SIZE[write_size_max]
	atoms = make_atoms(writes, sz, szmax, numbwritten, overflows, strategy, badbytes)

	return make_payload_dollar(offset, atoms, numbwritten, no_dollars=no_dollars)

def fmtstr_payload(offset, writes, numbwritten=0, write_size='byte', write_size_max='long', overflows=16, strategy="small", badbytes=frozenset(), offset_bytes=0, no_dollars=False):
	sz = WRITE_SIZE[write_size]
	szmax = WRITE_SIZE[write_size_max]
	all_atoms = make_atoms(writes, sz, szmax, numbwritten, overflows, strategy, badbytes)

	fmt = b""
	for _ in range(1000000):
		data_offset = (offset_bytes + len(fmt)) // context.bytes
		fmt, data = make_payload_dollar(offset + data_offset, all_atoms, numbwritten=numbwritten, no_dollars=no_dollars)
		fmt = fmt + cyclic((-len(fmt)-offset_bytes) % context.bytes)

		if len(fmt) + offset_bytes == data_offset * context.bytes:
			break
	else:
		raise RuntimeError("this is a bug ... format string building did not converge")

	return fmt + data