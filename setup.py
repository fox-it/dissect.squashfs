from setuptools import setup

setup(
    name="dissect.squashfs",
    packages=["dissect.squashfs"],
    install_requires=[
        "dissect.cstruct>=3.0.dev,<4.0.dev",
        "dissect.util>=3.0.dev,<4.0.dev",
    ],
    extras_require={
        "full": [
            "lz4",
            "python-lzo",
            "zstandard",
        ]
    },
)
