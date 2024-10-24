import subprocess, struct, itertools, warnings
from collections import namedtuple

elf_types = {1:"RELOC",2:"EXEC",3:"DYN",4:"CORE"}
seg_types = {0:"NULL",1:"LOAD",2:"DYNAMIC",3:"INTERP",4:"NOTE",
            5:"SHLIB",6:"PHDR",7:"TLS",1685382480:"GNU_EH_FRAME",
            1685382482:"GNU_RELRO",1685382481:"GNU_STACK",1685382483:"GNU_PROPERTY"}

def read_c_str(data): # read a null-terminated string in an array
    return bytes(itertools.takewhile((0).__ne__, data))

"""
A dumb ELF parser written by a dumb guy.
If this breaks on an edge case, I am terribly sorry.

Sources:
https://docs.oracle.com/cd/E19683-01/816-1386/chapter6-94076/index.html -- section headers
https://docs.oracle.com/cd/E19683-01/816-1386/chapter6-83432/index.html -- program heaaders
https://docs.oracle.com/cd/E19683-01/816-1386/chapter6-54839/index.html -- relocations (for GOT and PLT)
http://www.skyfree.org/linux/references/ELF_Format.pdf -- ELF header (oracle docs mostly regurgitate this but provide some 64bit info aswell)
https://github.com/Gallopsled/pwntools/blob/dev/pwnlib/elf/elf.py - pwntools elf library
https://github.com/edibledinos/pwnypack/blob/master/pwnypack/elf.py - pwnypack (pwntools v0) elf library
https://blog.k3170makan.com/2018/09/introduction-to-elf-file-format-part.html - all chapters of this blog
"""
class ELF:
    def __init__(self, path):
        self.path = path
        self.file = open(path,"rb")
        self._parse_header() # header bytes
        self._parse_ph() # program headers
        self._parse_sh() # section headers
        self._parse_sym() # determine symbol names & values
        self._parse_got_attempt_plt() # determine GOT names & values, attempt PLT resolution
    
    # self.unpack, but read 'format' bytes into the file
    def readunpack(self,f):
        data = self.file.read(struct.calcsize(self.se+f))
        return self.unpack(f,data)
    
    # struct.unpack, but respect the program's endianness
    def unpack(self,f,data):
        return struct.unpack(self.se+f,data)
        
    def _parse_header(self):
        e_ident = self.file.read(16)
        
        # magic bytes, 32/64bit, endianness, header version, abi version
        magic, self.bits, self.endian, self.hver, self.abi, _ = struct.unpack('IBBBBQ',e_ident)
        
        assert magic == 1179403647, "ELF magic bytes do not match"
        assert self.bits in (1,2), "Unknown bitlength (???)"
        assert self.endian in (1,2), "Unknown endianness (???)"
        self.endianness, self.se = ('little','<') if self.endian == 1 else ('big','>')
        self.bits *= 32 # quick way to set as 32-bit or 64-bit
        
        self.type, self.machine, self.ver = self.readunpack('HHI')
        self.type = elf_types[self.type]
        
        next_header = 'QQQIHHHHHH' if self.bits == 64 else 'IIIIHHHHHH'
        
        self.entry, self.phoff, self.shoff, self.flags, self.ehsize, \
        self.phentsize, self.phnum, self.shentsize, self.shnum, self.shstrndx = self.readunpack(next_header)
        
    def _parse_ph(self): # parse program headers
        phdr = 'IIQQQQQQ' if self.bits == 64 else 'IIIIIIII'
   
        self.file.seek(self.phoff)
        sections = []
        for _ in range(self.phnum):
            section = list(self.readunpack(phdr))
            section[0] = seg_types[section[0]] # parsing p_type
            sections.append(section)
        self.phent = sections
        # lowest 'LOAD' addr is the base addr
        self._address = 0
        if self.type != 'DYN':
            vaddr_idx = 3 if self.bits == 64 else 2
            self._address = min([x[vaddr_idx] for x in sections if x[0] == 'LOAD'])
    
    def _parse_sh(self): # parse section headers
        self.shent = {} 
        shdr = 'IIQQQQIIQQ' if self.bits == 64 else 'IIIIIIIIII'
        self.file.seek(self.shoff)
        sections = []
        for _ in range(self.shnum):
            section = list(self.readunpack(shdr))
            sections.append(section)
        self._shent = sections
        tabnames = self._read_section(self._shent[self.shstrndx])
        for i in range(self.shnum): # parsing p_name
            section_name = read_c_str(tabnames[sections[i][0]:])
            self.shent[section_name.decode()] = sections[i].copy()
            sections[i][0] = section_name

    def _parse_sym(self): # parse .symtab and .dynsym
        self._sym = {}
        self._symbols = self._parse_X_sym('.symtab','.strtab')
        self._dynsymbols = self._parse_X_sym('.dynsym','.dynstr')
        for x in (self._symbols+self._dynsymbols):
            # does this need to be more precise?
            # like, should i be worrying about a symbol's type?
            self._sym[x['name']] = x['value']
            
    def _parse_got_attempt_plt(self): # determine GOT pointers, attempt PLT stub resolution
        rela = 'QQq' if self.bits == 64 else 'IIi'
        relaplt = self.shent.get('.rela.plt',None)
        if not relaplt:
            relaplt = self.shent.get('.rel.plt',None) # check for .rel.plt
            if not relaplt: # nothing, so just quit
                return
            rela = 'QQ' if self.bits == 64 else 'II'
        entries = relaplt[5]//relaplt[9] # sh_size // sh_shentsize
        self.file.seek(relaplt[4]) # sh_offset
        self._got = {}
        
        rels = []
        for i in range(entries):
            entry = list(self.readunpack(rela))
            idx = entry[1] >> 32 if self.bits == 64 else entry[1] >> 8
            self._got[self._dynsymbols[idx]['name']] = entry[0]
            rels.append(entry)  
        rels = list(sorted(rels)) # sort by first entry, which is the offset
        
        """
        *Attempt* to parse PLT.
        This is a suprisingly difficult task-- the code below is a heuristic.
        More here: https://tinyurl.com/y9n4pvhx
        """
        self._plt = {}
        plt = self.shent['.plt']
        if not plt:
            return
            
        plt_entsize = plt[9]
        plt_entsize *= 1 if self.bits == 64 else 4 # i386 reports wrong size (???)
        plt_addr = plt[3]
        self.file.seek(relaplt[4]) # sh_offset
        
        for i,rel in enumerate(rels):
            idx = rel[1] >> 32 if self.bits == 64 else rel[1] >> 8
            if idx != 0:
                self._plt[self._dynsymbols[idx]['name']] = plt_addr+((i+1)*plt_entsize)

    # take a symbol table & its string table, return its parsed version
    def _parse_X_sym(self,symtab,strtab):
        elf32_sym = namedtuple('elf32_sym', 'name value size info other shndx')
        elf64_sym = namedtuple('elf64_sym', 'name info other shndx value size')
        sym = 'IBBHQQ' if self.bits == 64 else 'IIIBBH'
        elf_sym = elf64_sym if self.bits == 64 else elf32_sym
        
        table = self.shent.get(symtab,None)
        if not table:
            return []
        symnames = self._read_section(self.shent[strtab])
        
        symbols = []
        entries = table[5]//table[9] # sh_size // sh_shentsize
        self.file.seek(table[4]) # sh_offset
        for _ in range(entries):
            symbol = (elf_sym._make(self.readunpack(sym)))._asdict()
            symbol_name = read_c_str(symnames[symbol['name']:]).decode()
            symbol['name'] = symbol_name
            symbols.append(symbol)
        return symbols

    @property
    def address(self):
        return self._address
        
    @address.setter
    def address(self,x):
        sym = getattr(self,"_sym",None)
        got = getattr(self,"_got",None)
        plt = getattr(self,"_plt",None)
        if any([sym,got,plt]) and (self.type != 'DYN'):
            warnings.warn("This is not a dynamic executable, are you sure?")
        if sym:
            self._sym = {k:(v-self.address+x) for k,v in sym.items()}
        if got:
            self._got = {k:(v-self.address+x) for k,v in got.items()}
        if plt:
            self._plt = {k:(v-self.address+x) for k,v in plt.items()}
        self._address = x

    @property
    def sym(self):
        return self._sym
        
    @property
    def got(self):
        return self._got
        
    @property
    def plt(self):
        warnings.warn("PLT may be wildly inaccurate. See _parse_got_attempt_plt()")
        return self._plt
        
    def _read_section(self,strtab): # get raw bytes of a section header
        self.file.seek(strtab[4])
        data = self.file.read(strtab[5])
        return data