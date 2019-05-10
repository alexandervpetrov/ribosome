"""Functionality of this package partially adopted from [setuptools_scm] package."""

import pathlib

from . import errors
from . import git
from . import hg


HANDLERS = (git, hg)


def _detect_scm_handler(rootpath):
    if not rootpath.exists():
        raise errors.ScmError('Root path does not exists: {}'.format(rootpath))
    if not rootpath.is_dir():
        raise errors.ScmError('Root path is not directory: {}'.format(rootpath))
    for handler in HANDLERS:
        if handler.detect(rootpath):
            return handler
    raise errors.ScmError('No supported SCM found in directory: {}'.format(rootpath))


def describe(root):
    rootpath = pathlib.Path(root)
    handler = _detect_scm_handler(rootpath)
    return handler.describe(rootpath)


def archive(root, targetdir):
    rootpath = pathlib.Path(root)
    targetdir = pathlib.Path(targetdir)
    handler = _detect_scm_handler(rootpath)
    return handler.archive(rootpath, targetdir)
