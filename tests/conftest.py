from __future__ import annotations

import gzip
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


def absolute_path(filename: str) -> Path:
    return Path(__file__).parent / filename


def open_file(name: str, mode: str = "rb") -> Iterator[BinaryIO]:
    with absolute_path(name).open(mode) as fh:
        yield fh


def open_file_gz(name: str, mode: str = "rb") -> Iterator[BinaryIO]:
    with gzip.GzipFile(absolute_path(name), mode) as fh:
        yield fh


@pytest.fixture
def gzip_sqfs() -> Iterator[BinaryIO]:
    yield from open_file("_data/gzip.sqfs")


@pytest.fixture
def gzip_opts_sqfs() -> Iterator[BinaryIO]:
    yield from open_file("_data/gzip-opts.sqfs")


@pytest.fixture
def lz4_sqfs() -> Iterator[BinaryIO]:
    yield from open_file("_data/lz4.sqfs")


@pytest.fixture
def lzma_sqfs() -> Iterator[BinaryIO]:
    yield from open_file("_data/lzma.sqfs")


@pytest.fixture
def lzo_sqfs() -> Iterator[BinaryIO]:
    yield from open_file("_data/lzo.sqfs")


@pytest.fixture
def xz_sqfs() -> Iterator[BinaryIO]:
    yield from open_file("_data/xz.sqfs")


@pytest.fixture
def zstd_sqfs() -> Iterator[BinaryIO]:
    yield from open_file("_data/zstd.sqfs")
