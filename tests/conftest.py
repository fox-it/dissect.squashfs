import gzip
import os

import pytest


def absolute_path(filename):
    return os.path.join(os.path.dirname(__file__), filename)


def open_file(name, mode="rb"):
    with open(absolute_path(name), mode) as f:
        yield f


def open_file_gz(name, mode="rb"):
    with gzip.GzipFile(absolute_path(name), mode) as f:
        yield f


@pytest.fixture
def gzip_sqfs():
    yield from open_file("data/gzip.sqfs")


@pytest.fixture
def gzip_opts_sqfs():
    yield from open_file("data/gzip-opts.sqfs")


@pytest.fixture
def lz4_sqfs():
    yield from open_file("data/lz4.sqfs")


@pytest.fixture
def lzma_sqfs():
    yield from open_file("data/lzma.sqfs")


@pytest.fixture
def lzo_sqfs():
    yield from open_file("data/lzo.sqfs")


@pytest.fixture
def xz_sqfs():
    yield from open_file("data/xz.sqfs")


@pytest.fixture
def zstd_sqfs():
    yield from open_file("data/zstd.sqfs")
