from __future__ import annotations

from io import BytesIO

import pytest

from dissect.squashfs.c_squashfs import c_squashfs
from dissect.squashfs.exceptions import (
    NotADirectoryError,
    NotAFileError,
    NotASymlinkError,
)
from dissect.squashfs.squashfs import SquashFS


def _verify_filesystem(sqfs: SquashFS) -> None:
    assert sqfs.root.is_dir()
    assert sorted(sqfs.root.listdir().keys()) == [
        "dir",
        "file-with-xattr",
        "large-file",
        "small-file",
        "symlink-1",
        "symlink-2",
        "symlink-with-xattr",
    ]

    f1 = sqfs.get("small-file")
    assert f1.is_file()
    assert f1.size == 9
    assert str(f1.mtime) == "2022-12-05 18:53:05+00:00"
    assert f1.open().read() == b"contents\n"
    f2 = sqfs.get("large-file")
    assert f2.is_file()
    assert f2.size == 4177920
    assert f2.open().read() == b"".join([bytes([i] * 4096) for i in range(255)]) * 4

    s1 = sqfs.get("symlink-1")
    assert s1.is_symlink()
    assert s1.link == "small-file"
    s2 = sqfs.get("symlink-2")
    assert s2.is_symlink()
    assert s2.link == "dir/file_69"

    d1 = sqfs.get("dir")
    assert d1.is_dir()
    assert d1.size == 1507
    assert len(list(d1.iterdir())) == 100

    assert sqfs.get("dir/file_69").inode_number == s2.link_inode.inode_number

    with pytest.raises(NotAFileError):
        sqfs.get("dir").open()

    with pytest.raises(NotADirectoryError):
        sqfs.get("small-file").listdir()

    with pytest.raises(NotASymlinkError):
        assert sqfs.get("large-file").link


@pytest.mark.parametrize(
    ("sqfs", "compression_id"),
    [
        ("gzip_sqfs", c_squashfs.ZLIB_COMPRESSION),
        ("gzip_opts_sqfs", c_squashfs.ZLIB_COMPRESSION),
        ("lz4_sqfs", c_squashfs.LZ4_COMPRESSION),
        # Whether LZMA works seems to depend on if your machine has been blessed by the compression gods
        # Include the test data for reference material, but don't test against it
        # ("lzma_sqfs", c_squashfs.LZMA_COMPRESSION),
        ("lzo_sqfs", c_squashfs.LZO_COMPRESSION),
        ("xz_sqfs", c_squashfs.XZ_COMPRESSION),
        ("zstd_sqfs", c_squashfs.ZSTD_COMPRESSION),
    ],
)
def test_squashfs(sqfs: str, compression_id: int, request: pytest.FixtureRequest) -> None:
    sqfs = SquashFS(request.getfixturevalue(sqfs))
    assert sqfs.sb.compression == compression_id
    _verify_filesystem(sqfs)


def test_squashfs_unsupported_bigendian_3_0() -> None:
    """Test if we correctly detect a SquashFS 3.0 big-endian filesystem."""

    # Random SquashFS version 3.0 big-endian header found on binwalk GitHub issue tracker.
    buf = bytes.fromhex(
        "73717368000000b60000002042010d364200bc844200339440016800000300006b0400104003004ae64f330000000000"
        "001623000100000000001177ff8e0800000000000e303100000000000e3025000000000000000000000000000e234c00"
    )

    sb = c_squashfs.squashfs_super_block(buf)
    assert sb.s_magic == c_squashfs.SQUASHFS_MAGIC_SWAP
    assert sb.s_major == 0x300

    with pytest.raises(NotImplementedError, match="Unsupported squashfs pre-4.0 big-endian filesystem"):
        SquashFS(BytesIO(buf))
