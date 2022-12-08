from __future__ import annotations

import stat
import struct
from bisect import bisect_right
from datetime import datetime
from functools import cache, cached_property, lru_cache
from typing import BinaryIO, Iterator, Optional

from dissect.cstruct import Instance
from dissect.util import ts
from dissect.util.stream import RunlistStream

from dissect.squashfs import compression
from dissect.squashfs.c_squashfs import INODE_STRUCT_MAP, TYPE_MAP, c_squashfs
from dissect.squashfs.exceptions import (
    FileNotFoundError,
    NotADirectoryError,
    NotAFileError,
    NotASymlinkError,
)


class SquashFS:
    def __init__(self, fh: BinaryIO):
        self.fh = fh

        sb = c_squashfs.squashfs_super_block(fh)
        if sb.s_magic != c_squashfs.SQUASHFS_MAGIC:
            raise ValueError("Invalid squashfs superblock")

        # Only support squashfs 4.x for now
        if sb.s_major != 4:
            raise NotImplementedError("Unsupported squashfs version")

        self.sb = sb
        self.mkfs_time = ts.from_unix(self.sb.mkfs_time)
        self.block_size = self.sb.block_size
        self.block_log = self.sb.block_log
        self.flags = self.sb.flags
        self.major = self.sb.s_major
        self.minor = self.sb.s_minor
        self.size = self.sb.bytes_used

        self._compression_options = None
        if (self.sb.flags >> c_squashfs.SQUASHFS_COMP_OPT) & 1:
            self._compression_options = self._read_block(len(self.sb))[1]

        self._compression = compression.initialize(self.sb.compression, self._compression_options)

        self.id_table = self._read_table(self.sb.id_table_start, self.sb.no_ids, 4)
        self.lookup_table = self._read_table(self.sb.lookup_table_start, self.sb.inodes, 8)
        self.fragment_table = self._read_table(
            self.sb.fragment_table_start, self.sb.fragments, len(c_squashfs.squashfs_fragment_entry)
        )

        self.root = self.inode(self.sb.root_inode >> 16, self.sb.root_inode & 0xFFFF, name="/")

    def inode(
        self,
        block: int,
        offset: int,
        name: str = None,
        type: int = None,
        inode_number: int = None,
        parent: INode = None,
    ) -> INode:
        # squashfs inode numbers consist of a block number and offset in that block
        return INode(self, block, offset, name, type, inode_number, parent)

    def get(self, path, node=None):
        if isinstance(path, int):
            return self.inode(path >> 16, path & 0xFFFF)

        node = node or self.root

        parts = path.split("/")

        for part_num, part in enumerate(parts):
            if not part:
                continue

            if part == ".":
                continue

            if part == "..":
                node = node.parent or node
                continue

            while node.is_symlink() and part_num < len(parts):
                node = node.link_inode

            for entry in node.iterdir():
                if entry.name == part:
                    node = entry
                    break
            else:
                raise FileNotFoundError(f"File not found: {path}")

        return node

    def _read_table(self, offset: int, count: int, entry_size: int) -> list[int]:
        if not count or offset == c_squashfs.SQUASHFS_INVALID_BLK:
            return []

        num_bytes = count * entry_size
        num_blocks = (num_bytes + c_squashfs.SQUASHFS_METADATA_SIZE - 1) // c_squashfs.SQUASHFS_METADATA_SIZE

        self.fh.seek(offset)
        return c_squashfs.uint64[num_blocks](self.fh)

    def _read_metadata(self, block: int, offset: int, length: int) -> bytes:
        result = []
        while length:
            next_block, data = self._read_block(block)
            remaining = len(data) - offset

            if remaining <= length:
                result.append(data[offset:])
                length -= remaining
                block = next_block
                offset = 0
            else:
                result.append(data[offset : offset + length])
                offset += length
                break

        return block, offset, b"".join(result)

    @lru_cache(1024)
    def _read_block(self, block: int, length: int = None) -> tuple[int, bytes]:
        if length is not None:
            # Data block
            compressed = length & c_squashfs.SQUASHFS_COMPRESSED_BIT_BLOCK == 0
            length = length & ~c_squashfs.SQUASHFS_COMPRESSED_BIT_BLOCK
        else:
            # Metadata block
            self.fh.seek(block)
            length = c_squashfs.uint16(self.fh)

            compressed = length & c_squashfs.SQUASHFS_COMPRESSED_BIT == 0
            length = length & ~c_squashfs.SQUASHFS_COMPRESSED_BIT
            block += 2

        self.fh.seek(block)
        data = self.fh.read(length)

        if not compressed:
            return block + length, data

        if self._compression is None:
            raise RuntimeError(f"Tried to read compressed block {block} but no compression initialized")

        return block + length, self._compression.decompress(data, self.block_size)

    def _read_fragment(self, fragment: int, offset: int, length: int) -> bytes:
        entry = self._lookup_fragment(fragment)
        _, data = self._read_block(entry.start_block, entry.size)
        return data[offset : offset + length]

    @lru_cache(1024)
    def _lookup_id(self, id: int) -> int:
        block, offset = divmod(id * 4, c_squashfs.SQUASHFS_METADATA_SIZE)
        _, _, data = self._read_metadata(self.id_table[block], offset, 4)
        return struct.unpack("<I", data)[0]

    @lru_cache(1024)
    def _lookup_inode(self, inode_number: int) -> INode:
        if inode_number <= 0 or inode_number > self.sb.inodes:
            raise IndexError(f"inode number out of bounds (1, {self.sb.inodes}): {inode_number}")
        block, offset = divmod((inode_number - 1) * 8, c_squashfs.SQUASHFS_METADATA_SIZE)
        _, _, data = self._read_metadata(self.lookup_table[block], offset, 8)
        return self.get(struct.unpack("<Q", data)[0])

    @lru_cache(1024)
    def _lookup_fragment(self, fragment: int) -> bytes:
        fragment_offset = fragment * len(c_squashfs.squashfs_fragment_entry)
        block, offset = divmod(fragment_offset, c_squashfs.SQUASHFS_METADATA_SIZE)

        _, _, data = self._read_metadata(self.fragment_table[block], offset, len(c_squashfs.squashfs_fragment_entry))
        entry = c_squashfs.squashfs_fragment_entry(data)
        return entry

    def iter_inodes(self) -> Iterator[INode]:
        for inum in range(1, self.sb.inodes + 1):
            yield self._lookup_inode(inum)


class INode:
    def __init__(
        self,
        fs: SquashFS,
        block: int,
        offset: int,
        name: str = None,
        type: int = None,
        inode_number: int = None,
        parent: INode = None,
    ):
        self.fs = fs
        self.block = block
        self.offset = offset
        self.name = name
        self._type = type
        self._inode_number = inode_number
        self.parent = parent

    def __repr__(self) -> str:
        return f"<inode {self.inode_number} ({self.block}, {self.offset})>"

    @cached_property
    def header(self) -> Instance:
        base_struct = c_squashfs.squashfs_base_inode_header

        block = self.fs.sb.inode_table_start + self.block
        _, _, data = self.fs._read_metadata(block, self.offset, len(base_struct))

        header = base_struct(data)
        actual_struct = INODE_STRUCT_MAP.get(header.inode_type)

        if len(actual_struct) != len(base_struct):
            _, _, data = self.fs._read_metadata(block, self.offset, len(actual_struct))

        if actual_struct != base_struct:
            header = actual_struct(data)

        return header

    @property
    def inode_number(self) -> int:
        return self._inode_number or self.header.inode_number

    @property
    def type(self) -> int:
        return TYPE_MAP[self._type or self.header.inode_type]

    @property
    def mode(self) -> int:
        return self.header.mode | self.type

    @property
    def uid(self) -> int:
        return self.fs._lookup_id(self.header.uid)

    @property
    def guid(self) -> int:
        return self.fs._lookup_id(self.header.guid)

    @property
    def gid(self) -> int:
        return self.guid

    @property
    def mtime(self) -> datetime:
        return ts.from_unix(self.header.mtime)

    @property
    def size(self) -> Optional[int]:
        if self.is_dir() or self.is_file():
            return self.header.file_size
        elif self.is_symlink():
            return self.header.symlink_size

    def is_dir(self) -> bool:
        return self.type == stat.S_IFDIR

    def is_file(self) -> bool:
        return self.type == stat.S_IFREG

    def is_symlink(self) -> bool:
        return self.type == stat.S_IFLNK

    def is_block_device(self) -> bool:
        return self.type == stat.S_IFBLK

    def is_character_device(self) -> bool:
        return self.type == stat.S_IFCHR

    def is_device(self) -> bool:
        return self.is_block_device() or self.is_character_device()

    def is_fifo(self) -> bool:
        return self.type == stat.S_IFIFO

    def is_socket(self) -> bool:
        return self.type == stat.S_IFSOCK

    def is_ipc(self) -> bool:
        return self.is_fifo() or self.is_socket()

    @cached_property
    def link(self) -> str:
        if not self.is_symlink():
            raise NotASymlinkError(f"{self!r} is not a symlink")

        _, _, data = self.fs._read_metadata(
            self.fs.sb.inode_table_start + self.block,
            self.offset + len(self.header),
            self.header.symlink_size,
        )
        return data.decode(errors="surrogateescape")

    @cached_property
    def link_inode(self) -> INode:
        link = self.link
        if link.startswith("/"):
            relnode = None
        else:
            relnode = self.parent
        return self.fs.get(self.link, relnode)

    @cache
    def listdir(self) -> dict[str, INode]:
        return {inode.name: inode for inode in self.iterdir()}

    def iterdir(self) -> Iterator[INode]:
        if not self.is_dir():
            raise NotADirectoryError(f"{self!r} is not a directory")

        if self.size == 3:
            return

        start = self.fs.sb.directory_table_start + self.header.start_block
        offset = self.header.offset

        bytes_read = 0
        while bytes_read < self.size - 3:
            start, offset, data = self.fs._read_metadata(start, offset, len(c_squashfs.squashfs_dir_header))
            dir_header = c_squashfs.squashfs_dir_header(data)
            bytes_read += len(data)

            for _ in range(dir_header.count + 1):
                start, offset, data = self.fs._read_metadata(start, offset, len(c_squashfs.squashfs_dir_entry))
                dir_entry = c_squashfs.squashfs_dir_entry(data)
                bytes_read += len(data)

                start, offset, data = self.fs._read_metadata(start, offset, dir_entry.size + 1)
                bytes_read += len(data)

                yield self.fs.inode(
                    dir_header.start_block,
                    dir_entry.offset,
                    data.decode(errors="surrogateescape"),
                    dir_entry.type,
                    dir_header.inode_number + dir_entry.inode_number,
                    parent=self,
                )

    @cached_property
    def block_list(self) -> list[tuple[Optional[int], int]]:
        fragment = self.header.fragment
        file_size = self.header.file_size
        if fragment == c_squashfs.SQUASHFS_INVALID_FRAG:
            frag_bytes = 0
            blocks = (file_size + self.fs.block_size - 1) >> self.fs.block_log
        else:
            blocks, frag_bytes = divmod(file_size, self.fs.block_size)

        if blocks:
            _, _, data = self.fs._read_metadata(
                self.fs.sb.inode_table_start + self.block,
                self.offset + len(self.header),
                blocks * 4,
            )
            block_list = [(block, 1) for block in c_squashfs.uint32[blocks](data)]
        else:
            block_list = []

        if frag_bytes:
            block_list.append((None, frag_bytes))

        return block_list

    def open(self) -> FileStream:
        if not self.is_file():
            raise NotAFileError(f"{self!r} is not a file")

        return FileStream(self)


class FileStream(RunlistStream):
    def __init__(self, inode: INode):
        super().__init__(inode.fs.fh, inode.block_list, inode.size, inode.fs.block_size)
        self.inode = inode
        self.fs = inode.fs
        self.start_block = inode.header.start_block
        self.fragment = inode.header.fragment
        self.fragment_offset = inode.header.offset

    def _read(self, offset: int, length: int) -> bytes:
        r = []

        block_offset = offset // self.block_size

        run_idx = bisect_right(self._runlist_offsets, block_offset)
        runlist_len = len(self.runlist)
        size = self.size

        start_block = self.start_block + sum(
            v & ~c_squashfs.SQUASHFS_COMPRESSED_BIT_BLOCK for v, _ in self.runlist[:run_idx]
        )

        while length > 0:
            if run_idx >= runlist_len:
                # We somehow requested more data than we have runs for
                break

            run_block_size, run_block_count = self.runlist[run_idx]

            # For squashfs we use 0 to indicate a sparse block and None to indicate a fragment
            if run_block_size is None:
                r.append(self.fs._read_fragment(self.fragment, self.fragment_offset, run_block_count))
                offset += run_block_count
                length -= run_block_count
            else:
                run_pos = offset - run_idx * self.block_size
                run_remaining = self.block_size - run_pos

                # Sometimes the self.size is way larger than what we actually have runs for?
                # Stop reading if we reach a negative run_remaining
                if run_remaining < 0:
                    break

                read_count = min(size - offset, min(run_remaining, length))

                # Sparse run
                if run_block_size == 0:
                    r.append(b"\x00" * read_count)
                else:
                    start_block, data = self.fs._read_block(start_block, run_block_size)
                    r.append(data[:read_count])

                offset += read_count
                length -= read_count
                run_idx += 1

        return b"".join(r)
