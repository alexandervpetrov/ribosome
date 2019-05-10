
import logging
import pathlib
import tempfile

from . import utils
from . import errors

log = logging.getLogger('scmtools.git')


def detect(rootpath):
    rootpath = pathlib.Path(rootpath)
    scmdir = rootpath.joinpath('.git')
    return scmdir.exists() and scmdir.is_dir()


def ensure_git_command_available(rootpath):
    try:
        utils.run('git help'.split(), cwd=rootpath)
    except errors.CommandRunError as e:
        raise errors.ScmError('Command [git] is not available') from e


def describe(rootpath):
    ensure_git_command_available(rootpath)

    output = utils.run(
        'git rev-parse --show-toplevel'.split(),
        cwd=rootpath,
        errormsg='Failed to identify repository',
    )
    gitroot = output

    if not gitroot:
        raise errors.ScmError('Failed to identify repository')
    if not rootpath.samefile(pathlib.Path(gitroot)):
        raise errors.ScmError('Failed to identify repository: root paths detected not match')

    shallow_file_path = rootpath.joinpath('.git').joinpath('shallow')
    if shallow_file_path.is_file():
        raise errors.ScmError('Repository is shallow clone. Cannot reliably work on shallow')

    branch = get_branch(rootpath)

    try:
        description = get_latest_tag_description(rootpath)
    except errors.CommandRunError:
        # probably latest tag does not exist
        latest_tag = None
        revision = get_revision(rootpath)
        distance = get_all_commits(rootpath)
        dirty = get_dirty_status(rootpath)
    else:
        latest_tag, distance, revision, dirty = description

    scminfo = dict(
        scm='git',
        revision=revision,
        branch=branch,
        tag=latest_tag,
        distance=distance,
        dirty=dirty,
    )
    return scminfo


def archive(rootpath, targetdir):
    ensure_git_command_available(rootpath)
    log.debug('Making archive of Git repo [%s] to [%s]: starting...', rootpath, targetdir)
    with tempfile.TemporaryDirectory() as tempdir:
        archive = pathlib.Path(tempdir).joinpath('archive.tar')
        utils.run(
            ['git', 'archive', '--format=tar', '--output={}'.format(archive), 'HEAD'],
            cwd=rootpath,
            errormsg='Failed to make repository archive',
        )
        utils.run(
            ['tar', '--extract', '--file={}'.format(archive), '--directory={}'.format(targetdir)],
            cwd=rootpath,
            errormsg='Failed to extract repository archive to target directory',
        )
    log.info('Making archive of Git repo [%s] to [%s]: done', rootpath, targetdir)


def get_branch(rootpath):
    output = utils.run(
        'git rev-parse --abbrev-ref HEAD'.split(),
        cwd=rootpath,
        errormsg='Failed to get branch',
    )
    branch = output
    return branch


def get_revision(rootpath):
    output = utils.run(
        'git rev-parse --verify --quiet HEAD'.split(),
        cwd=rootpath,
        errormsg='Failed to get revision',
    )
    revision = output[:7]
    return revision


def get_latest_tag_description(rootpath):
    output = utils.run(
        # 'git describe --dirty --tags --long --match *.*'.split(),  # only tags with dots inside allowed
        'git describe --dirty --tags --long --match *'.split(),  # any tags allowed
        cwd=rootpath,
        errormsg='Failed to describe repository',
    )
    repo_description = output

    dirty = False
    if repo_description.endswith('-dirty'):
        dirty = True
        repo_description = repo_description[:-6]

    tag, distance, revision = repo_description.rsplit("-", 2)
    distance = int(distance)
    revision = revision[1:]

    return (tag, distance, revision, dirty)


def get_all_commits(rootpath):
    output = utils.run(
        'git rev-list HEAD'.split(),
        cwd=rootpath,
        errormsg='Failed to get commit history',
    )
    commits_count = len(output.splitlines())
    return commits_count


def get_dirty_status(rootpath):
    output = utils.run(
        'git status --porcelain --untracked-files=no'.split(),
        cwd=rootpath,
        errormsg='Failed to get dirty status',
    )
    is_dirty = bool(output)
    return is_dirty
